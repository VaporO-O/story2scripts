"""Durable local tasks: commit before publish, atomic claims and bounded recovery.

SQLite is authoritative. Per-job OS locks prevent two local stores/processes
from running the same job during recovery. Progress subscriptions remain local;
this is deliberately not a distributed task system.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from uuid import uuid4

from .job_lock import JobFileLock
from .security import redact_secrets
from .redis_runtime import redis_enabled, append_event
from .redis_dispatch import OUTBOX_SCHEMA

JOBS_DB_ENV = "STORY2SCRIPT_JOBS_DB"
JOBS_WORKERS_ENV = "STORY2SCRIPT_JOBS_WORKERS"
DEFAULT_DB_DIRNAME = ".story2script"
DEFAULT_DB_FILENAME = "jobs.db"
CANCELLED_MESSAGE = "任务在排队时被取消。"
_MAX_RESTART_ATTEMPTS = 3
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
    "revision",
    "idempotency_key",
    "generation",
)
_SCHEMA = """CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0)"""
logger = logging.getLogger(__name__)
_JOB_DATABASE = ContextVar("story2script_job_database", default=None)


class JobStorageError(RuntimeError):
    """No durable success is reported when task storage cannot be accessed."""


class JobConflictError(RuntimeError):
    pass


def _now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _workers():
    try:
        return max(1, int(os.getenv(JOBS_WORKERS_ENV, "2")))
    except ValueError:
        return 2


def resolve_db_path(db_path=None):
    value = db_path or _JOB_DATABASE.get() or os.getenv(JOBS_DB_ENV, "").strip()
    return (
        Path(value).expanduser() if value else Path.cwd() / DEFAULT_DB_DIRNAME / DEFAULT_DB_FILENAME
    )


_SCHEMA_LOCK = threading.Lock()


def _connect(path):
    # WAL initialization is not covered by SQLite's ordinary busy timeout on all
    # platforms. Serialize first-use setup locally and retry cross-process races.
    with _SCHEMA_LOCK:
        for attempt in range(4):
            conn = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
                if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    conn.execute("PRAGMA journal_mode=WAL")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
                index = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' AND name='jobs_idempotency'"
                ).fetchone()
                outbox = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='job_outbox'"
                ).fetchone()
                if (
                    not {"revision", "idempotency_key", "generation"} <= columns
                    or not index
                    or not outbox
                ):
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(_SCHEMA)
                    # Another process may have migrated while we waited for the transaction.
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
                    for column, definition in (
                        ("revision", "INTEGER NOT NULL DEFAULT 0"),
                        ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
                        ("generation", "INTEGER NOT NULL DEFAULT 0"),
                    ):
                        if column not in columns:
                            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency ON jobs(kind,idempotency_key) WHERE idempotency_key != ''"
                    )
                    conn.execute(OUTBOX_SCHEMA)
                    conn.commit()
                return conn
            except (OSError, sqlite3.Error) as exc:
                if conn:
                    conn.close()
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and "locked" in str(exc).lower()
                    and attempt < 3
                ):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise JobStorageError(
                    "任务存储不可用，未确认保存成功。请检查磁盘空间、目录权限与数据库占用后重试。"
                ) from exc


def _read_rows(path, sql, params=()):
    conn = _connect(path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]
    except sqlite3.Error as exc:
        raise JobStorageError("无法读取任务存储，请稍后重试。") from exc
    finally:
        conn.close()


def list_jobs(limit=50, db_path=None, kind=None):
    path = resolve_db_path(db_path)
    if not path.exists():
        return []
    sql = "SELECT job_id,kind,status,progress,stage,message,error,attempts,created_at,updated_at FROM jobs"
    params = []
    if kind:
        sql += " WHERE kind=?"
        params.append(kind)
    sql += " ORDER BY updated_at DESC,created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    return _read_rows(path, sql, params)


class DurableJobStore:
    kind = ""
    request_model = None
    result_model = None
    response_model = None
    queued_stage = "等待执行"
    queued_message = "任务已保存，等待执行。"
    complete_stage = "执行完成"
    fail_stage = "执行失败"

    def __init__(self, db_path=None):
        self._db_override = db_path
        self._lock = threading.RLock()
        self._rows = {}
        self._paths = {}
        self._active = set()
        self._scheduled = set()
        self._reschedule = set()
        self._storage_errors = {}
        self._subscribers = {}
        self._events = {}
        self._executor = ThreadPoolExecutor(max_workers=_workers())
        self._startup_error = None
        try:
            self._recover()
        except JobStorageError as exc:
            # Keep the API available to explain a bad database, never fall back to memory execution.
            self._startup_error = exc
            logger.error("Task recovery unavailable: %s", exc)

    def _db_path(self):
        return resolve_db_path(self._db_override).absolute()

    def _job_path(self, job_id):
        return self._paths.get(job_id, self._db_path())

    def _retry_startup_recovery(self):
        with self._lock:
            if self._startup_error is None:
                return
            self._recover()
            self._startup_error = None

    def subscribe(self, job_id):
        queue = Queue()
        with self._lock:
            backlog = list(self._events.get(job_id, ()))
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue, backlog

    def unsubscribe(self, job_id, queue):
        with self._lock:
            queues = self._subscribers.get(job_id, [])
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id, event):
        with self._lock:
            if job_id in self._storage_errors:
                raise self._storage_errors[job_id]
            self._publish_locked(job_id, event)

    def _publish_locked(self, job_id, event):
        if redis_enabled():
            row = self._rows.get(job_id) or self._row(job_id)
            append_event(f"{job_id}:{row['generation']}:{row['attempts']}", event)
            return
        self._events.setdefault(job_id, deque(maxlen=_EVENT_MIRROR_LIMIT)).append(event)
        for queue in self._subscribers.get(job_id, ()):
            queue.put(event)

    def _discard_events_locked(self, job_id):
        if job_id not in self._subscribers:
            self._events.pop(job_id, None)

    def discard_events_if_idle(self, job_id):
        with self._lock:
            row = self._rows.get(job_id)
            if row is None or row["status"] in ("succeeded", "failed"):
                self._discard_events_locked(job_id)

    def _enqueue(self, job_id, *, after_active=False):
        if redis_enabled():
            from .redis_dispatch import dispatch_pending

            dispatch_pending(self._job_path(job_id), job_id)
            return
        with self._lock:
            if job_id in self._scheduled:
                if after_active:
                    self._reschedule.add(job_id)
                return
            self._scheduled.add(job_id)
        try:
            self._executor.submit(self._execute, job_id)
        except RuntimeError as exc:
            with self._lock:
                self._scheduled.discard(job_id)
            self._fail(job_id, "任务执行器不可用；任务已保存，请恢复后重试。")
            raise JobStorageError("任务已保存，但执行器不可用，请恢复任务。") from exc

    def create(self, request, *, idempotency_key=""):
        self._retry_startup_recovery()
        if len(idempotency_key) > 128:
            raise JobConflictError("请求去重标识不能超过 128 个字符。")
        canonical = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        job_id = str(uuid4())
        now = _now_iso()
        row = dict(
            job_id=job_id,
            kind=self.kind,
            status="queued",
            progress=0,
            stage=self.queued_stage,
            message=self.queued_message,
            error="",
            request_json=canonical,
            result_json="",
            attempts=0,
            created_at=now,
            updated_at=now,
            revision=0,
            idempotency_key=idempotency_key,
            generation=0,
        )
        path = self._db_path()
        self._paths[job_id] = path
        conn = _connect(path)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = (
                    conn.execute(
                        "SELECT job_id,request_json FROM jobs WHERE kind=? AND idempotency_key=?",
                        (self.kind, idempotency_key),
                    ).fetchone()
                    if idempotency_key
                    else None
                )
                if existing:
                    if json.loads(existing[1]) != json.loads(canonical):
                        raise JobConflictError("相同请求标识对应的内容已变化，请使用新的标识。")
                    self._paths.pop(job_id, None)
                    job_id = existing[0]
                    self._paths[job_id] = path
                else:
                    conn.execute(
                        f"INSERT INTO jobs ({','.join(_COLUMNS)}) VALUES ({','.join('?' for _ in _COLUMNS)})",
                        tuple(row[c] for c in _COLUMNS),
                    )
                if redis_enabled():
                    conn.execute(
                        "INSERT OR IGNORE INTO job_outbox(job_id,generation) SELECT job_id,generation FROM jobs WHERE job_id=? AND status IN ('queued','running')",
                        (job_id,),
                    )
        except sqlite3.Error as exc:
            self._paths.pop(job_id, None)
            raise JobStorageError("任务未保存，未开始执行。请检查任务数据库后重试。") from exc
        finally:
            conn.close()
        row = self._row(job_id)
        if row["status"] == "queued":
            self._enqueue(job_id)
        return self.snapshot(job_id)

    def find_request(self, key):
        self._retry_startup_recovery()
        rows = _read_rows(
            self._db_path(),
            "SELECT job_id FROM jobs WHERE kind=? AND idempotency_key=?",
            (self.kind, key),
        )
        if not key or not rows:
            raise KeyError(key)
        return self.snapshot(rows[0]["job_id"])

    def snapshot(self, job_id):
        self._retry_startup_recovery()
        self._repair_storage_failure(job_id)
        row = self._row(job_id)
        if redis_enabled() and row["status"] == "queued":
            dispatch = _read_rows(
                self._job_path(job_id),
                "SELECT last_error FROM job_outbox WHERE job_id=? AND generation=?",
                (job_id, row["generation"]),
            )
            if dispatch and dispatch[0]["last_error"]:
                row = {**row, "message": dispatch[0]["last_error"]}
        result = None
        if row["status"] == "succeeded":
            try:
                result = self.result_model.model_validate_json(row["result_json"])
            except Exception as exc:
                raise JobStorageError("保存的任务结果损坏，不能报告成功。") from exc
        return self.response_model(
            job_id=job_id,
            status=row["status"],
            progress=row["progress"],
            stage=row["stage"],
            message=row["message"],
            result=result,
            error=row["error"],
        )

    def has_job(self, job_id):
        try:
            self._row(job_id)
            return True
        except KeyError:
            return False

    def cancel(self, job_id):
        with self._lock:
            row = self._row(job_id)
            if row["status"] != "queued":
                raise ValueError("任务已开始执行，无法取消。")
            self._transition(
                row,
                status="failed",
                stage="已取消",
                message=CANCELLED_MESSAGE,
                error=CANCELLED_MESSAGE,
            )
            self._publish_locked(
                job_id, {"type": "done", "status": "failed", "message": CANCELLED_MESSAGE}
            )

    def resume(self, job_id):
        self._repair_storage_failure(job_id)
        with self._lock:
            row = self._row(job_id)
            if row["status"] != "failed":
                raise ValueError("只有已中断的任务可以恢复。")
            if row["stage"] == "已取消":
                raise ValueError("已取消的任务请重新提交。")
            if row["attempts"] >= _MAX_RESTART_ATTEMPTS:
                raise ValueError("任务已达恢复次数上限，请检查失败原因后重新提交。")
            self._transition(
                row,
                status="queued",
                stage="等待恢复",
                message="从已保存结果继续。",
                error="",
                generation=row["generation"] + 1,
            )
        self._enqueue(job_id, after_active=True)
        return self.snapshot(job_id)

    def _execute(self, job_id, *, generation=None, wait_for_lock=True):
        execution_lock = None
        context = _JOB_DATABASE.set(self._job_path(job_id))
        try:
            if generation is not None and self._row(job_id)["generation"] != generation:
                return
            execution_lock = JobFileLock(self._job_path(job_id), job_id)
            # Recovery waits for an existing local executor. If it dies, OS releases the lock.
            while not execution_lock.acquire():
                if not wait_for_lock:
                    return  # Durable outbox will redeliver if the live owner dies.
                if self._row(job_id)["status"] in ("succeeded", "failed"):
                    return
                time.sleep(0.1)
            with self._lock:
                self._active.add(job_id)
            if generation is not None and self._row(job_id)["generation"] != generation:
                return
            if not self._claim(job_id):
                return
            self._run(job_id)
            if self._row(job_id)["status"] == "running":
                self._fail(job_id, "执行器退出但未保存任务结果，请恢复任务。")
        except JobStorageError as exc:
            self._record_storage_failure(job_id, exc)
        except JobConflictError:
            logger.warning("Job state changed concurrently: %s", job_id)
        except Exception as exc:
            try:
                self._fail(job_id, str(exc))
            except JobStorageError as storage:
                self._record_storage_failure(job_id, storage)
        finally:
            owns_execution = bool(execution_lock and execution_lock.locked)
            if execution_lock:
                execution_lock.close()
            with self._lock:
                if owns_execution or wait_for_lock:
                    self._active.discard(job_id)
                    self._scheduled.discard(job_id)
                again = job_id in self._reschedule if owns_execution or wait_for_lock else False
                if again:
                    self._reschedule.discard(job_id)
            _JOB_DATABASE.reset(context)
            if again:
                self._enqueue(job_id)

    def _claim(self, job_id):
        with self._lock:
            row = self._row(job_id)
            if row["status"] not in ("queued", "running"):
                return False
            if row["status"] == "running" and job_id not in self._active:
                return False
            if row["attempts"] >= _MAX_RESTART_ATTEMPTS:
                self._transition(
                    row,
                    status="failed",
                    stage=self.fail_stage,
                    error="进程重启中断，已达重试上限。",
                    message="进程重启中断，已达重试上限。",
                )
                return False
            try:
                self.request_model.model_validate_json(row["request_json"])
            except ValueError:
                self._transition(
                    row,
                    status="failed",
                    stage=self.fail_stage,
                    error="任务数据损坏，无法恢复。",
                    message="任务数据损坏，无法恢复。",
                )
                return False
            self._transition(row, status="running", attempts=row["attempts"] + 1, error="")
            return True

    def _run(self, job_id):
        raise NotImplementedError

    def _request_for(self, job_id):
        return self.request_model.model_validate_json(self._row(job_id)["request_json"])

    def _update(self, job_id, *, status="running", progress, stage, message=""):
        if status != "running":
            raise ValueError("进度更新只能用于执行中的任务。")
        with self._lock:
            row = self._row(job_id)
            if row["status"] != "running":
                raise JobConflictError("已结束的任务不能回退为执行中。")
            progress = min(99, max(row["progress"], progress))
            values = dict(status=status, progress=progress, stage=stage, message=message or stage)
            if all(row[k] == v for k, v in values.items()):
                return
            self._transition(row, **values)
            self._publish_locked(job_id, {"type": "progress", **values})

    def _complete(self, job_id, result):
        with self._lock:
            row = self._row(job_id)
            if row["status"] == "succeeded":
                return
            if row["status"] != "running":
                raise JobConflictError("只有执行中的任务可以提交结果。")
            self._transition(
                row,
                status="succeeded",
                progress=100,
                stage=self.complete_stage,
                message=self._complete_message(result),
                error="",
                result_json=result.model_dump_json(),
            )
            self._publish_locked(
                job_id,
                {
                    "type": "done",
                    "status": "succeeded",
                    "progress": 100,
                    "stage": self.complete_stage,
                    "message": self._complete_message(result),
                },
            )
            self._discard_events_locked(job_id)

    def _complete_message(self, result):
        return "任务完成。"

    def _fail(self, job_id, error):
        with self._lock:
            row = self._row(job_id)
            if row["status"] in ("succeeded", "failed"):
                return
            safe = redact_secrets(error)
            self._transition(row, status="failed", stage=self.fail_stage, message=safe, error=safe)
            self._publish_locked(
                job_id,
                {
                    "type": "done",
                    "status": "failed",
                    "stage": self.fail_stage,
                    "message": safe,
                    "error": safe,
                },
            )
            self._discard_events_locked(job_id)

    def _transition(self, row, **values):
        replacement = {**row, **values, "updated_at": _now_iso()}
        self._persist(replacement)
        self._rows[row["job_id"]] = replacement
        return replacement

    def _persist(self, row):
        path = self._job_path(row["job_id"])
        conn = _connect(path)
        expected = row["revision"]
        fields = [
            c
            for c in _COLUMNS
            if c
            not in ("job_id", "kind", "created_at", "revision", "idempotency_key", "request_json")
        ]
        try:
            with conn:
                changed = conn.execute(
                    f"UPDATE jobs SET {','.join(c + '=?' for c in fields)},revision=revision+1 WHERE job_id=? AND kind=? AND revision=?",
                    [row[c] for c in fields] + [row["job_id"], self.kind, expected],
                ).rowcount
                if changed != 1:
                    raise JobConflictError("任务状态已被更新，请重新查询。")
                if row["status"] in ("succeeded", "failed"):
                    conn.execute(
                        "DELETE FROM job_outbox WHERE job_id=? AND generation=?",
                        (row["job_id"], row["generation"]),
                    )
                elif row["status"] == "queued" and redis_enabled():
                    conn.execute(
                        "INSERT OR IGNORE INTO job_outbox(job_id,generation) VALUES(?,?)",
                        (row["job_id"], row["generation"]),
                    )
            row["revision"] = expected + 1
        except sqlite3.Error as exc:
            raise JobStorageError(
                "任务状态或结果未能保存，不能报告成功。请检查存储后恢复任务。"
            ) from exc
        finally:
            conn.close()

    def _load_row(self, job_id):
        rows = _read_rows(
            self._job_path(job_id),
            f"SELECT {','.join(_COLUMNS)} FROM jobs WHERE job_id=? AND kind=?",
            (job_id, self.kind),
        )
        return rows[0] if rows else None

    def _row(self, job_id):
        row = self._load_row(job_id)
        if row is None:
            raise KeyError(job_id)
        with self._lock:
            self._paths.setdefault(job_id, self._db_path())
            self._rows[job_id] = row
        return row

    def _record_storage_failure(self, job_id, exc):
        with self._lock:
            self._storage_errors[job_id] = exc
        logger.error("Task persistence failed for %s: %s", job_id, exc)
        try:
            self._fail(job_id, "任务存储失败，执行已停止；检查存储后可从检查点恢复。")
        except (JobStorageError, JobConflictError):
            return
        with self._lock:
            self._storage_errors.pop(job_id, None)

    def _repair_storage_failure(self, job_id):
        with self._lock:
            error = self._storage_errors.get(job_id)
            if error and job_id in self._active:
                # The executor is persisting the terminal failure after a storage
                # error. Until it finishes, callers may safely observe the last
                # durable state (usually running), but must not see a transient 500.
                return
        if error:
            self._record_storage_failure(job_id, error)
            if job_id in self._storage_errors:
                raise error

    def _recover(self):
        path = self._db_path()
        if not path.exists():
            return
        if redis_enabled():
            conn = _connect(path)
            try:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO job_outbox(job_id,generation) SELECT job_id,generation FROM jobs WHERE kind=? AND status IN ('queued','running')",
                        (self.kind,),
                    )
            finally:
                conn.close()
            # Worker repair loop dispatches these; web startup never executes work.
            return
        rows = _read_rows(
            path,
            "SELECT job_id FROM jobs WHERE kind=? AND status IN ('queued','running') ORDER BY created_at",
            (self.kind,),
        )
        for row in rows:
            self._paths[row["job_id"]] = path
            self._enqueue(row["job_id"])
