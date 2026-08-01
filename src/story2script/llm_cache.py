"""进程级 LLM 响应缓存：相同请求确定性重放，避免重复计费。

- 线程安全 LRU（`STORY2SCRIPT_LLM_CACHE_MAX_ENTRIES`，默认 256）；
- 可选磁盘层（`STORY2SCRIPT_LLM_CACHE_DIR`）：写入 <sha256>.json，内存 miss
  时回源磁盘，跨进程复用；写失败静默降级为仅内存；
- 总开关 `STORY2SCRIPT_LLM_CACHE_DISABLE`；
- 模块级单例：LLMClient 每次请求都新建实例，实例级缓存没有意义。

语义提示：缓存意味着"同请求返回同响应"。重新生成类操作（场景重写）与
分块转换的重试路径必须绕过缓存，由调用方传 use_cache=False 控制。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

CACHE_DISABLE_ENV = "STORY2SCRIPT_LLM_CACHE_DISABLE"
CACHE_MAX_ENTRIES_ENV = "STORY2SCRIPT_LLM_CACHE_MAX_ENTRIES"
CACHE_DIR_ENV = "STORY2SCRIPT_LLM_CACHE_DIR"
DEFAULT_MAX_ENTRIES = 256
_DISABLED_VALUES = {"1", "true", "yes", "on"}


def cache_key(*parts: object) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _cache_disabled() -> bool:
    return os.getenv(CACHE_DISABLE_ENV, "").strip().lower() in _DISABLED_VALUES


def _max_entries() -> int:
    raw = os.getenv(CACHE_MAX_ENTRIES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_ENTRIES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_ENTRIES


def _cache_dir() -> Path | None:
    raw = os.getenv(CACHE_DIR_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


class LLMResponseCache:
    """LRU + 可选磁盘回源。value 为任意可 JSON 序列化对象（str / list[float]）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, object] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> object | None:
        if _cache_disabled():
            return None
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self._hits += 1
                return self._entries[key]
        value = self._read_disk(key)
        with self._lock:
            if value is not None:
                self._store(key, value)
                self._hits += 1
            else:
                self._misses += 1
        return value

    def put(self, key: str, value: object) -> None:
        if _cache_disabled():
            return
        with self._lock:
            self._store(key, value)
        self._write_disk(key, value)

    def _store(self, key: str, value: object) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        limit = _max_entries()
        while len(self._entries) > limit:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }

    # ------------------------------------------------------------------ 磁盘层

    def _disk_path(self, key: str) -> Path | None:
        directory = _cache_dir()
        if directory is None:
            return None
        return directory / f"{key}.json"

    def _read_disk(self, key: str) -> object | None:
        path = self._disk_path(key)
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_disk(self, key: str, value: object) -> None:
        path = self._disk_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError):
            pass


llm_cache = LLMResponseCache()
