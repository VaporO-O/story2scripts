"""Optional Redis transport. Credentials stay in environment and never in errors."""

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class RedisUnavailable(RuntimeError):
    pass


def redis_enabled():
    backend = os.getenv("STORY2SCRIPT_TASK_BACKEND", "local").strip().lower()
    if backend not in ("local", "redis"):
        raise ValueError("STORY2SCRIPT_TASK_BACKEND 只能是 local 或 redis。")
    return backend == "redis"


def namespace():
    value = os.getenv("STORY2SCRIPT_REDIS_NAMESPACE", "story2script").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("Redis 命名空间只允许 1–64 个字母、数字、下划线或连字符。")
    return value


def redis_url():
    value = os.getenv("STORY2SCRIPT_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("redis", "rediss") or not parsed.hostname:
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ValueError(
            "STORY2SCRIPT_REDIS_URL 必须为有效的 redis:// 或 rediss:// 地址。"
        ) from None
    return value


def number(name, default, minimum=1, maximum=86400):
    try:
        value = int(os.getenv(name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError()
        return value
    except ValueError:
        raise ValueError(f"{name} 必须在 {minimum}–{maximum} 之间。") from None


@lru_cache(maxsize=8)
def _client(url):
    try:
        from redis import Redis
    except ImportError:
        raise RedisUnavailable('请先安装 Redis 支持：pip install -e ".[redis]"。') from None
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=3,
        health_check_interval=30,
        max_connections=32,
    )


def redis_client():
    return _client(redis_url())


def key_for(part, identity=""):
    return f"{namespace()}:{part}" + (":" + identity if identity else "")


def cache_enabled():
    value = os.getenv("STORY2SCRIPT_CACHE_BACKEND", "local").lower()
    if value not in ("local", "redis"):
        raise ValueError("STORY2SCRIPT_CACHE_BACKEND 只能是 local 或 redis。")
    return value == "redis"


def cache_get(key):
    try:
        payload = redis_client().get(key_for("cache:v1", key))
        return json.loads(payload) if payload is not None else None
    except Exception:
        logger.warning("Redis cache unavailable; treating lookup as a cache miss.")
        return None


def cache_put(key, value):
    payload = json.dumps(value, ensure_ascii=False)
    if len(payload.encode()) > number(
        "STORY2SCRIPT_REDIS_CACHE_MAX_BYTES", 2_000_000, maximum=20_000_000
    ):
        return
    ttl = number("STORY2SCRIPT_REDIS_CACHE_TTL", 86400, maximum=2_592_000)
    try:
        redis_client().set(key_for("cache:v1", key), payload, ex=ttl)
    except Exception:
        logger.warning("Redis cache write unavailable; generation result is still returned.")


def append_event(job_id, event):
    """Progress is disposable. Failure here must not undo a committed job result."""
    ttl = number("STORY2SCRIPT_REDIS_EVENT_TTL", 86400, maximum=604800)
    limit = number("STORY2SCRIPT_REDIS_EVENT_LIMIT", 1000, maximum=100000)
    try:
        client = redis_client()
        stream = key_for("events", job_id)
        with client.pipeline(transaction=True) as pipe:
            pipe.xadd(
                stream,
                {"payload": json.dumps(event, ensure_ascii=False)},
                maxlen=limit,
                approximate=False,
            )
            pipe.expire(stream, ttl)
            pipe.execute()
    except Exception:
        logger.warning("Redis progress delivery unavailable; database polling remains available.")


def read_events(job_id, cursor="0-0"):
    if not re.fullmatch(r"\d+-\d+", cursor):
        raise ValueError("无效的进度事件游标。")
    try:
        rows = redis_client().xread({key_for("events", job_id): cursor}, count=100)
        return [
            (event_id, json.loads(fields["payload"]))
            for _, entries in rows
            for event_id, fields in entries
        ]
    except Exception:
        raise RedisUnavailable(
            "Redis 进度暂不可用，请通过任务状态接口查询；任务结果仍保存在数据库。"
        ) from None


_RATE_SCRIPT = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local count = tonumber(redis.call('HGET', KEYS[1], 'count') or '0')
local start = tonumber(redis.call('HGET', KEYS[1], 'start') or now)
if now >= start + window then count = 0; start = now end
if count >= limit then return {0, start + window - now} end
redis.call('HSET', KEYS[1], 'count', count + 1, 'start', start)
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""


def rate_limit(provider, credential, operation):
    """Atomic shared fixed-window RPM. Never silently bypass an enabled limit."""
    setting = "STORY2SCRIPT_EMBED_RPM" if operation == "embedding" else "STORY2SCRIPT_LLM_RPM"
    limit = number(setting, 0, minimum=0, maximum=1_000_000)
    if not limit:
        return
    # API key hash is scoped to a provider account; plaintext is never a Redis key.
    identity = hashlib.sha256(
        json.dumps([provider.rstrip("/"), credential, operation]).encode()
    ).hexdigest()
    try:
        allowed, wait = redis_client().eval(
            _RATE_SCRIPT, 1, key_for("rate", identity), limit, 60000
        )
    except Exception:
        error = ValueError("Redis 全局限流服务不可用，本次模型请求未发出，请恢复服务后重试。")
        error.error_kind = "rate_limit_unavailable"
        error.status_code = 503
        raise error from None
    if not allowed:
        error = ValueError(
            f"已达到共享模型调用上限，约 {(wait + 999) // 1000} 秒后可重试；本次请求未发出。"
        )
        error.error_kind = "rate_limit"
        error.status_code = 429
        raise error
