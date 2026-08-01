"""进程内指标注册表：LLM 调用与任务执行的成功率 / 延迟 / Token 消耗。

设计要点：
- 累计聚合（次数、成败、耗时总和、token 总和）永不丢失；环形事件缓冲
  （STORY2SCRIPT_METRICS_MAX_EVENTS，默认 500）用于 p50/p95 与最近事件查询。
- threading.Lock 保护：转换分块与审校都在线程池里并发调 LLM。
- 可选 JSONL 落盘（STORY2SCRIPT_METRICS_LOG）：逐事件追加一行 JSON，
  写失败静默停写，不影响业务。
- 本模块不 import 包内其他模块，避免循环依赖。
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

MAX_EVENTS_ENV = "STORY2SCRIPT_METRICS_MAX_EVENTS"
METRICS_LOG_ENV = "STORY2SCRIPT_METRICS_LOG"
DEFAULT_MAX_EVENTS = 500


def _max_events() -> int:
    raw = os.getenv(MAX_EVENTS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_EVENTS
    try:
        return max(10, int(raw))
    except ValueError:
        return DEFAULT_MAX_EVENTS


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _percentile(sorted_values: list[int], ratio: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, max(0, round(ratio * (len(sorted_values) - 1))))
    return sorted_values[index]


class _Aggregate:
    """单个维度（label 或任务 kind）的累计聚合。"""

    __slots__ = (
        "count",
        "success",
        "duration_ms_total",
        "prompt_tokens",
        "completion_tokens",
        "cache_hits",
        "last_error",
    )

    def __init__(self) -> None:
        self.count = 0
        self.success = 0
        self.duration_ms_total = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hits = 0
        self.last_error = ""


def _summary_row(aggregate: _Aggregate, durations: list[int]) -> dict:
    count = aggregate.count
    ordered = sorted(durations)
    row = {
        "calls": count,
        "success": aggregate.success,
        "failure": count - aggregate.success,
        "success_rate": round(aggregate.success / count, 4) if count else 0.0,
        "avg_ms": round(aggregate.duration_ms_total / count) if count else 0,
        "p50_ms": _percentile(ordered, 0.5),
        "p95_ms": _percentile(ordered, 0.95),
        "prompt_tokens": aggregate.prompt_tokens,
        "completion_tokens": aggregate.completion_tokens,
        "total_tokens": aggregate.prompt_tokens + aggregate.completion_tokens,
        "cache_hits": aggregate.cache_hits,
    }
    if aggregate.last_error:
        row["last_error"] = aggregate.last_error
    return row


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=_max_events())
        self._llm: dict[str, _Aggregate] = {}
        self._tasks: dict[str, _Aggregate] = {}
        self._started_at = _now_iso()
        self._sink_disabled = False

    # ------------------------------------------------------------------ 记录

    def record_llm_call(
        self,
        label: str,
        model: str = "",
        duration_ms: int = 0,
        ok: bool = True,
        error_kind: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prompt_chars: int = 0,
        response_chars: int = 0,
        cached: bool = False,
    ) -> None:
        event = {
            "type": "llm_call",
            "at": _now_iso(),
            "label": label,
            "model": model,
            "duration_ms": int(duration_ms),
            "ok": bool(ok),
            "error_kind": error_kind,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "prompt_chars": int(prompt_chars or 0),
            "response_chars": int(response_chars or 0),
            "cached": bool(cached),
        }
        with self._lock:
            aggregate = self._llm.setdefault(label, _Aggregate())
            aggregate.count += 1
            aggregate.duration_ms_total += event["duration_ms"]
            aggregate.prompt_tokens += event["prompt_tokens"]
            aggregate.completion_tokens += event["completion_tokens"]
            if cached:
                aggregate.cache_hits += 1
            if ok:
                aggregate.success += 1
            else:
                aggregate.last_error = error_kind
            self._events.append(event)
        self._write_sink(event)

    def record_task(
        self,
        kind: str,
        mode: str = "",
        duration_ms: int = 0,
        ok: bool = True,
        error: str = "",
        extra: dict | None = None,
    ) -> None:
        event = {
            "type": "task",
            "at": _now_iso(),
            "kind": kind,
            "mode": mode,
            "duration_ms": int(duration_ms),
            "ok": bool(ok),
            "error": error[:200],
            "extra": extra or {},
        }
        with self._lock:
            aggregate = self._tasks.setdefault(kind, _Aggregate())
            aggregate.count += 1
            aggregate.duration_ms_total += event["duration_ms"]
            if ok:
                aggregate.success += 1
            else:
                aggregate.last_error = event["error"]
            self._events.append(event)
        self._write_sink(event)

    # ------------------------------------------------------------------ 查询

    def summary(self) -> dict:
        with self._lock:
            llm_durations: dict[str, list[int]] = {}
            task_durations: dict[str, list[int]] = {}
            for event in self._events:
                if event["type"] == "llm_call":
                    llm_durations.setdefault(event["label"], []).append(event["duration_ms"])
                else:
                    task_durations.setdefault(event["kind"], []).append(event["duration_ms"])

            llm = {
                label: _summary_row(aggregate, llm_durations.get(label, []))
                for label, aggregate in sorted(self._llm.items())
            }
            tasks = {
                kind: {
                    key: value
                    for key, value in _summary_row(
                        aggregate, task_durations.get(kind, [])
                    ).items()
                    if not key.endswith("_tokens") and key != "cache_hits"
                }
                for kind, aggregate in sorted(self._tasks.items())
            }
            overall = _Aggregate()
            for aggregate in self._llm.values():
                overall.count += aggregate.count
                overall.success += aggregate.success
                overall.duration_ms_total += aggregate.duration_ms_total
                overall.prompt_tokens += aggregate.prompt_tokens
                overall.completion_tokens += aggregate.completion_tokens
                overall.cache_hits += aggregate.cache_hits
            all_llm_durations = [d for values in llm_durations.values() for d in values]
            return {
                "generated_at": _now_iso(),
                "since": self._started_at,
                "llm_overall": _summary_row(overall, all_llm_durations),
                "llm": llm,
                "tasks": tasks,
            }

    def recent_events(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            return list(self._events)[-limit:][::-1]

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._llm.clear()
            self._tasks.clear()
            self._started_at = _now_iso()
            self._sink_disabled = False

    # ------------------------------------------------------------------ 落盘

    def _write_sink(self, event: dict) -> None:
        if self._sink_disabled:
            return
        path_text = os.getenv(METRICS_LOG_ENV, "").strip()
        if not path_text:
            return
        try:
            path = Path(path_text).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as sink:
                sink.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            self._sink_disabled = True


metrics = MetricsRegistry()
