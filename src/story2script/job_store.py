"""SQLite 持久化任务队列基类：任务落盘、重启恢复、排队可取消、历史可查。

设计：
- **落盘 + 内存镜像**：写路径 write-through（内存 dict 更新 + 一条 SQLite
  UPSERT）；``snapshot()`` 只读内存（MCP 以 5 次/秒轮询），上个进程留下的
  任务在内存 miss 时按需从 DB 回源。
- **重启恢复**：store 构造时把本 kind 的 running 行重新排队（attempts 达上限
  则判失败），queued 行重新提交执行——单例在模块导入时创建，恢复随之生效。
- **取消**：仅对还在排队的任务生效；为不扩散 ConvertJobStatus Literal 与
  前端终态判断，取消统一落为 status=failed、stage="已取消"。
- 存储接口（create/snapshot/cancel/list + JSON 任务体）与 Redis/RQ 语义对齐，
  未来替换后端时业务管线与路由零改动。
- 落盘失败静默降级为纯内存运行（与 metrics JSONL 落盘同策略），不阻塞业务。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from uuid import uuid4

from .security import redact_secrets

JOBS_DB_ENV = "STORY2SCRIPT_JOBS_DB"
JOBS_WORKERS_ENV = "STORY2SCRIPT_JOBS_WORKERS"
DEFAULT_DB_DIRNAME = ".story2script"
DEFAULT_DB_FILENAME = "jobs.db"
CANCELLED_MESSAGE = "任务在排队时被取消。"
_MAX_RESTART_ATTEMPTS = 3
# 重连补发用的内存镜像上限。一篇长篇小说约几十个场景加几十条进度，留足余量；
# 有界是为了防止某个没人看的任务把内存拖大。
_EVENT_MIRROR_LIMIT = 500

_COLUMNS = (
    "job_id",
    "kind",
    "status",
    "progress",
    "stage",
    "message",
    "error",
    "request_json",
    "result_json",
    "attempts",
    "created_at",
    "updated_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _workers() -> int:
    raw = os.getenv(JOBS_WORKERS_ENV, "").strip()
    if not raw:
        return 2
    try:
        return max(1, int(raw))
    except ValueError:
        return 2


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """任务库路径：显式参数 → STORY2SCRIPT_JOBS_DB → cwd/.story2script/jobs.db。

    惰性解析（每次连接时调用），保证测试在 import 单例之后设置的环境变量
    依然生效。
    """
    if db_path:
        return Path(db_path).expanduser()
    env = os.getenv(JOBS_DB_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return Path.cwd() / DEFAULT_DB_DIRNAME / DEFAULT_DB_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    return conn


def list_jobs(limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    """列出最近任务（两类 kind 合并，轻量字段，不含 request/result 大 JSON）。"""
    limit = max(1, min(int(limit), 200))
    path = resolve_db_path(db_path)
    if not path.is_file():
        return []
    try:
        conn = _connect(path)
        try:
            cursor = conn.execute(
                "SELECT job_id, kind, status, progress, stage, message, error, attempts, "
                "created_at, updated_at FROM jobs "
                "ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (limit,),
            )
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


class DurableJobStore:
    """子类提供 kind、四个模型/文案类属性与业务 ``_run``；本基类负责持久化。"""

    kind: str = ""
    request_model: type = None  # type: ignore[assignment]
    result_model: type = None  # type: ignore[assignment]
    response_model: type = None  # type: ignore[assignment]
    queued_stage: str = "等待执行"
    queued_message: str = "任务已创建，等待后端开始处理。"
    complete_stage: str = "执行完成"
    fail_stage: str = "执行失败"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_override = db_path
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        # 事件通道：订阅者队列 + 一份内存镜像供重连补发。
        # 事件不进 SQLite——_persist 每次都要新建连接并重写整行（含整篇小说的
        # request_json），把逐场景事件写进去会把磁盘写放大成噪音；而且这些事件
        # 只对"正在观看的这一次转换"有意义，重启后没有补发价值。
        self._subscribers: dict[str, list[Queue]] = {}
        self._events: dict[str, deque] = {}
        self._executor = ThreadPoolExecutor(max_workers=_workers())
        self._recover()

    # ------------------------------------------------------------------ 事件

    def subscribe(self, job_id: str) -> tuple[Queue, list[dict]]:
        """注册订阅者，同时取回已发生的事件。

        补发与注册必须在同一把锁内完成：先读镜像再注册的话，两步之间到达的
        事件谁都不收（漏一个事件就丢一个场景）；先注册再读镜像则会重复。
        """
        queue: Queue = Queue()
        with self._lock:
            backlog = list(self._events.get(job_id, ()))
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue, backlog

    def unsubscribe(self, job_id: str, queue: Queue) -> None:
        """移除订阅者。客户端断开时必须调用，否则关掉的标签页会留下一个持续增长的队列。"""
        with self._lock:
            queues = self._subscribers.get(job_id)
            if not queues:
                return
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict) -> None:
        with self._lock:
            self._publish_locked(job_id, event)

    def _publish_locked(self, job_id: str, event: dict) -> None:
        """在已持有 self._lock 的前提下扇出事件（_update / _complete / _fail 复用）。"""
        mirror = self._events.setdefault(job_id, deque(maxlen=_EVENT_MIRROR_LIMIT))
        mirror.append(event)
        for queue in self._subscribers.get(job_id, ()):
            queue.put(event)

    def _discard_events_locked(self, job_id: str) -> None:
        """任务终结且无人订阅时丢弃镜像，避免长跑进程里累积。

        还有订阅者时保留：它们可能尚未读完队列，镜像要等它们退订后再由
        ``discard_events_if_idle`` 清掉。没人看过的任务在这里当场释放。
        """
        if job_id not in self._subscribers:
            self._events.pop(job_id, None)

    def discard_events_if_idle(self, job_id: str) -> None:
        """最后一个订阅者退订后调用：任务已终结就释放镜像。"""
        with self._lock:
            if job_id in self._subscribers:
                return
            row = self._rows.get(job_id)
            if row is None or row["status"] in ("succeeded", "failed"):
                self._events.pop(job_id, None)

    # ------------------------------------------------------------------ 公共面

    def create(self, request):
        job_id = str(uuid4())
        now = _now_iso()
        row = {
            "job_id": job_id,
            "kind": self.kind,
            "status": "queued",
            "progress": 0,
            "stage": self.queued_stage,
            "message": self.queued_message,
            "error": "",
            "request_json": request.model_dump_json(),
            "result_json": "",
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
            "_request_obj": request,
        }
        with self._lock:
            self._rows[job_id] = row
            self._persist(row)
        self._executor.submit(self._execute, job_id)
        return self.snapshot(job_id)

    def snapshot(self, job_id: str):
        row = self._row(job_id)
        result = row.get("_result_obj")
        if result is None and row["result_json"]:
            try:
                result = self.result_model.model_validate_json(row["result_json"])
                row["_result_obj"] = result
            except Exception:
                result = None
        return self.response_model(
            job_id=row["job_id"],
            status=row["status"],
            progress=row["progress"],
            stage=row["stage"],
            message=row["message"],
            result=result,
            error=row["error"],
        )

    def has_job(self, job_id: str) -> bool:
        try:
            self._row(job_id)
            return True
        except KeyError:
            return False

    def cancel(self, job_id: str) -> None:
        """取消还在排队的任务；已开始/已结束的任务抛 ValueError。"""
        with self._lock:
            row = self._rows.get(job_id)
            if row is None:
                row = self._load_row(job_id)
                if row is None:
                    raise KeyError(job_id)
                self._rows[job_id] = row
            if row["status"] != "queued":
                raise ValueError("任务已开始执行，无法取消。")
            row.update(
                status="failed",
                stage="已取消",
                message=CANCELLED_MESSAGE,
                error=CANCELLED_MESSAGE,
                updated_at=_now_iso(),
            )
            self._persist(row)

    # ------------------------------------------------------------------ 执行

    def _execute(self, job_id: str) -> None:
        if not self._claim(job_id):
            return
        self._run(job_id)

    def _claim(self, job_id: str) -> bool:
        with self._lock:
            row = self._rows.get(job_id)
            if row is None or row["status"] != "queued":
                return False
            row["status"] = "running"
            row["attempts"] += 1
            row["updated_at"] = _now_iso()
            self._persist(row)
            return True

    def _run(self, job_id: str) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    # ------------------------------------------------ 子类沿用的既有辅助签名

    def _request_for(self, job_id: str):
        row = self._row(job_id)
        request = row.get("_request_obj")
        if request is None:
            request = self.request_model.model_validate_json(row["request_json"])
            row["_request_obj"] = request
        return request

    def _update(
        self,
        job_id: str,
        *,
        status: str = "running",
        progress: int,
        stage: str,
        message: str = "",
    ) -> None:
        resolved_message = message or stage
        with self._lock:
            row = self._rows[job_id]
            # 细粒度进度会把更新次数从个位数拉到几十次，而每次 _persist 都要新建
            # 连接并重写整行（含整篇小说的 request_json）。四个字段都没变就直接
            # 返回，避免把磁盘写放大成噪音。
            if (
                row["status"] == status
                and row["progress"] == progress
                and row["stage"] == stage
                and row["message"] == resolved_message
            ):
                return
            row.update(
                status=status,
                progress=progress,
                stage=stage,
                message=resolved_message,
                updated_at=_now_iso(),
            )
            self._persist(row)
            # 在同一把锁内扇出：轮询只能看到落盘的四个字段、且会漏掉两次 poll
            # 之间的变化——对进度无妨，对场景是致命的（漏一个事件就丢一个场景）。
            self._publish_locked(
                job_id,
                {
                    "type": "progress",
                    "status": status,
                    "progress": progress,
                    "stage": stage,
                    "message": resolved_message,
                },
            )

    def _complete(self, job_id: str, result) -> None:
        with self._lock:
            row = self._rows[job_id]
            row.update(
                status="succeeded",
                progress=100,
                stage=self.complete_stage,
                message=self._complete_message(result),
                error="",
                result_json=result.model_dump_json(),
                updated_at=_now_iso(),
            )
            row["_result_obj"] = result
            self._persist(row)
            # 终态事件只带状态不带整份结果：结果可能是一整篇剧本，走既有的
            # snapshot 路由取回即可，不必塞进事件流复制一遍。
            self._publish_locked(
                job_id,
                {
                    "type": "done",
                    "status": "succeeded",
                    "progress": 100,
                    "stage": row["stage"],
                    "message": row["message"],
                },
            )
            self._discard_events_locked(job_id)

    def _complete_message(self, result) -> str:
        return "任务完成。"

    def _fail(self, job_id: str, error: str) -> None:
        # 单点脱敏：两类任务的 DB error 列、API error 字段与前端展示都经由这里。
        safe_error = redact_secrets(error)
        with self._lock:
            row = self._rows[job_id]
            row.update(
                status="failed",
                stage=self.fail_stage,
                message=safe_error,
                error=safe_error,
                updated_at=_now_iso(),
            )
            self._persist(row)
            self._publish_locked(
                job_id,
                {
                    "type": "done",
                    "status": "failed",
                    "progress": row["progress"],
                    "stage": row["stage"],
                    "message": safe_error,
                    "error": safe_error,
                },
            )
            self._discard_events_locked(job_id)

    # ------------------------------------------------------------------ 存储

    def _db_path(self) -> Path:
        return resolve_db_path(self._db_override)

    def _persist(self, row: dict) -> None:
        updates = ",".join(
            f"{column}=excluded.{column}"
            for column in _COLUMNS
            if column not in ("job_id", "kind", "created_at")
        )
        try:
            conn = _connect(self._db_path())
            try:
                with conn:
                    conn.execute(
                        f"INSERT INTO jobs ({','.join(_COLUMNS)}) "
                        f"VALUES ({','.join('?' * len(_COLUMNS))}) "
                        f"ON CONFLICT(job_id) DO UPDATE SET {updates}",
                        tuple(row[column] for column in _COLUMNS),
                    )
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def _load_row(self, job_id: str) -> dict | None:
        path = self._db_path()
        if not path.is_file():
            return None
        try:
            conn = _connect(path)
            try:
                cursor = conn.execute(
                    f"SELECT {','.join(_COLUMNS)} FROM jobs WHERE job_id = ? AND kind = ?",
                    (job_id, self.kind),
                )
                record = cursor.fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        if record is None:
            return None
        return dict(zip(_COLUMNS, record))

    def _row(self, job_id: str) -> dict:
        with self._lock:
            if job_id in self._rows:
                return self._rows[job_id]
        loaded = self._load_row(job_id)
        if loaded is None:
            raise KeyError(job_id)
        with self._lock:
            return self._rows.setdefault(job_id, loaded)

    # ------------------------------------------------------------------ 恢复

    def _recover(self) -> None:
        path = self._db_path()
        if not path.is_file():
            return
        try:
            conn = _connect(path)
            try:
                cursor = conn.execute(
                    f"SELECT {','.join(_COLUMNS)} FROM jobs "
                    "WHERE kind = ? AND status IN ('queued', 'running')",
                    (self.kind,),
                )
                records = [dict(zip(_COLUMNS, record)) for record in cursor.fetchall()]
            finally:
                conn.close()
        except sqlite3.Error:
            return

        for row in records:
            try:
                row["_request_obj"] = self.request_model.model_validate_json(
                    row["request_json"]
                )
            except Exception:
                error = "任务数据损坏，无法恢复。"
                row.update(
                    status="failed",
                    stage=self.fail_stage,
                    message=error,
                    error=error,
                    updated_at=_now_iso(),
                )
                with self._lock:
                    self._rows[row["job_id"]] = row
                    self._persist(row)
                continue

            if row["status"] == "running":
                if row["attempts"] >= _MAX_RESTART_ATTEMPTS:
                    error = "进程重启中断，已达重试上限。"
                    row.update(
                        status="failed",
                        stage=self.fail_stage,
                        message=error,
                        error=error,
                        updated_at=_now_iso(),
                    )
                    with self._lock:
                        self._rows[row["job_id"]] = row
                        self._persist(row)
                    continue
                row["status"] = "queued"
                row["updated_at"] = _now_iso()

            with self._lock:
                self._rows[row["job_id"]] = row
                self._persist(row)
            self._executor.submit(self._execute, row["job_id"])
