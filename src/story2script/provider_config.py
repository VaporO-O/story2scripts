"""API 供应商配置：多套命名配置的保存、切换与遮罩读取。

为什么能做到「实时」切换：项目里没有任何模块级 LLMClient 单例——`get_converter()`
每次新建实例，`LLMClient.__init__` 每次重读 `.env`。所以把激活的配置写回 `.env`
之后，下一个请求自然就用新配置，无需重启进程；已在跑的任务保持旧配置（不该中途
换供应商），这正是想要的行为。

存储分两处，各有原因：
- `.story2script/providers.json`：多套配置的清单（含密钥原文——切回旧供应商需要
  它）。该目录已被 .gitignore 忽略，与 jobs.db 同处一地。
- `.env`：只存**当前激活**的那一套。写它而不是让应用直接读 providers.json，是因为
  `LLMClient` 本来就读 `.env`，这样 LLM 层零改动，且独立进程（MCP server）也能跟着切。

安全边界：
- 只允许写白名单内的 `AI_*` 键。否则这就成了「经 HTTP 往磁盘写任意环境变量」的口子。
- 读取一律遮罩密钥，明文只进磁盘、不出 API。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .job_store import DEFAULT_DB_DIRNAME
from .llm_client import (
    _default_env_path,
    _dotenv_is_disabled,
    _load_env_file,
    _parse_env_line,
)

# 保存 / 切换 / 删除都要读改写两个文件，并发请求交错会写坏清单或 .env。
_write_lock = threading.Lock()

PROVIDERS_FILENAME = "providers.json"
PROVIDERS_DIR_ENV = "STORY2SCRIPT_PROVIDERS_DIR"

# 白名单：只有这些键能经 API 落到 .env。多一个口子就多一处「远程写任意配置」的风险。
# 与 llm_client 的属性一一对应。
PROVIDER_FIELDS: tuple[str, ...] = (
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_API_KEY",
    "AI_WIRE_API",
    "AI_REASONING_EFFORT",
    "AI_DISABLE_RESPONSE_STORAGE",
    "AI_TIMEOUT_SECONDS",
    "AI_MAX_TOKENS",
    "AI_MAX_CONCURRENCY",
    "AI_CHAPTER_CHUNK_CHARS",
    "AI_RETRY_BACKOFF_SECONDS",
    "AI_EMBED_MODEL",
)

# 需要遮罩的字段：明文只存磁盘，不经 API 返回。
SECRET_FIELDS = frozenset({"AI_API_KEY"})

# 切换供应商时必须齐全的三项，缺了 LLMClient 会在调用时报配置错误。
REQUIRED_FIELDS = ("AI_BASE_URL", "AI_MODEL", "AI_API_KEY")

_NAME_PATTERN = re.compile(r"^[\w.\- ]{1,40}$", re.UNICODE)


MASK_CHAR = "•"


def mask_secret(value: str) -> str:
    """把密钥遮罩成 `••••1234`；太短就整体遮掉，不泄漏长度线索之外的内容。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return MASK_CHAR * len(value)
    return f"{MASK_CHAR * 4}{value[-4:]}"


def looks_like_masked_secret(value: str) -> bool:
    """判断传进来的密钥是不是我们自己发出去的遮罩值。

    真实密钥不可能含 `•`，所以这个判断没有误伤风险。必须在服务端拦：任何
    「GET 读出来再 POST 回去」的客户端（脚本、另一个前端、浏览器密码管理器
    自动填充）都会把 `••••1234` 原样交回来，照字面存下就等于**用遮罩值覆盖掉
    真密钥**——配置从此不可用，且原值已丢失。前端不回填只是第一道防线。
    """
    return MASK_CHAR in (value or "")


def validate_profile_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("配置名不能为空。")
    if not _NAME_PATTERN.match(name):
        raise ValueError("配置名只能包含字母、数字、下划线、点、短横线和空格，且不超过 40 字符。")
    return name


def _providers_dir() -> Path:
    override = os.getenv(PROVIDERS_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return Path.cwd() / DEFAULT_DB_DIRNAME


def providers_path() -> Path:
    return _providers_dir() / PROVIDERS_FILENAME


def env_path() -> Path:
    # 复用 LLMClient 的解析函数，保证「这里写进去的」就是「那边读出来的」。
    # 各写一份路径逻辑迟早会漂移，切换就会静默不生效。
    return _default_env_path()


def sanitize_fields(fields: dict) -> dict[str, str]:
    """只保留白名单内的键，值统一转成去空白的字符串。"""
    cleaned: dict[str, str] = {}
    for key in PROVIDER_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _read_store() -> dict:
    path = providers_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"active": "", "profiles": {}}
    except (OSError, json.JSONDecodeError):
        # 配置清单坏了不该让工作台起不来：退回空清单，用户可重新保存。
        return {"active": "", "profiles": {}}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    return {
        "active": str(raw.get("active", "")),
        "profiles": {
            str(name): sanitize_fields(fields)
            for name, fields in profiles.items()
            if isinstance(fields, dict)
        },
    }


def _write_store(store: dict) -> None:
    path = providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        # 文件里有密钥原文，尽量收紧权限。Windows 上 chmod 语义有限，失败不阻塞。
        path.chmod(0o600)
    except OSError:
        pass


def shadowed_fields() -> list[str]:
    """返回被进程环境变量遮盖的字段。

    `_config_value` 的优先级是 os.getenv 优先于 .env，所以若某个 AI_* 已存在于
    进程环境里，写 .env 是**不生效**的。这类静默失效必须让用户看到。
    """
    return [name for name in PROVIDER_FIELDS if os.getenv(name) is not None]


def dotenv_disabled() -> bool:
    """`STORY2SCRIPT_DISABLE_DOTENV=1` 时 LLMClient 完全不读 .env。

    这种情况下切换配置会「写了文件但毫无效果」——比字段被单个遮盖更彻底的
    静默失效，必须一并暴露给前端。
    """
    return _dotenv_is_disabled()


def write_env_values(values: dict[str, str]) -> None:
    """把配置合并进 `.env`，保留注释与本模块不管的键。

    白名单内的键按「全量替换」处理：配置里没有的就从 .env 删掉。否则从推理模型切到
    非推理模型时，上一套的 AI_REASONING_EFFORT 会残留下来，造成跨配置串味。
    """
    path = env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as exc:
        raise ValueError(f"无法读取 .env：{exc}") from exc

    kept: list[str] = []
    for line in lines:
        parsed = _parse_env_line(line)
        if parsed is not None and parsed[0] in PROVIDER_FIELDS:
            continue  # 由下方统一重写
        kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()

    body = [f"{key}={values[key]}" for key in PROVIDER_FIELDS if key in values]
    content = "\n".join([*kept, *body]).strip("\n")
    try:
        path.write_text(content + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        raise ValueError(f"无法写入 .env：{exc}") from exc


def current_env_fields() -> dict[str, str]:
    """读当前生效的配置（进程环境优先，与 LLMClient 的解析口径一致）。"""
    file_values = _load_env_file(env_path())
    resolved: dict[str, str] = {}
    for key in PROVIDER_FIELDS:
        value = os.getenv(key)
        if value is None:
            value = file_values.get(key, "")
        value = value.strip()
        if value:
            resolved[key] = value
    return resolved


def public_profile(name: str, fields: dict[str, str], active: bool) -> dict:
    """给 API 用的投影：密钥只出遮罩值。"""
    masked = {
        key: (mask_secret(value) if key in SECRET_FIELDS else value)
        for key, value in fields.items()
    }
    return {
        "name": name,
        "active": active,
        "fields": masked,
        "has_api_key": bool(fields.get("AI_API_KEY")),
        "missing_fields": [key for key in REQUIRED_FIELDS if not fields.get(key)],
    }


def list_profiles() -> dict:
    store = _read_store()
    active = store["active"]
    return {
        "active": active,
        "profiles": [
            public_profile(name, fields, name == active)
            for name, fields in sorted(store["profiles"].items())
        ],
        "current": {
            key: (mask_secret(value) if key in SECRET_FIELDS else value)
            for key, value in current_env_fields().items()
        },
        "shadowed_fields": shadowed_fields(),
        "dotenv_disabled": dotenv_disabled(),
        "env_path": str(env_path()),
    }


def save_profile(name: str, fields: dict, activate: bool = False) -> dict:
    """新增或更新一套配置。

    密钥留空表示「保持原值」：前端拿到的是遮罩值，原样回传不应把密钥冲掉。
    """
    name = validate_profile_name(name)
    incoming = sanitize_fields(fields)
    # 客户端把读到的遮罩值原样交回来时，按「没填」处理，让下面的 SECRET_FIELDS
    # 分支保住原密钥。照字面存下会用 "••••1234" 覆盖掉真密钥且原值无法找回。
    for key in SECRET_FIELDS:
        if looks_like_masked_secret(incoming.get(key, "")):
            incoming.pop(key, None)
    # 「提交了但为空」与「没提交」是两回事：前者表示要清掉这一项。缺了这个区分，
    # 同一套配置从推理模型改成非推理模型时，旧的 AI_REASONING_EFFORT 会残留。
    submitted = {key for key in fields if key in PROVIDER_FIELDS}
    with _write_lock:
        store = _read_store()
        existing = store["profiles"].get(name, {})

        merged = dict(existing)
        merged.update(incoming)
        for key in submitted:
            # 密钥例外：前端拿到的是遮罩值，留空一律理解为「保持原密钥」，
            # 否则每次改别的字段都会把密钥冲掉。
            if key in SECRET_FIELDS or incoming.get(key):
                continue
            merged.pop(key, None)
        for key in SECRET_FIELDS:
            if not incoming.get(key) and existing.get(key):
                merged[key] = existing[key]

        store["profiles"][name] = merged
        if activate:
            store["active"] = name
        _write_store(store)
        if activate:
            write_env_values(merged)
        return list_profiles()


def activate_profile(name: str) -> dict:
    name = validate_profile_name(name)
    with _write_lock:
        store = _read_store()
        fields = store["profiles"].get(name)
        if fields is None:
            raise KeyError(name)
        missing = [key for key in REQUIRED_FIELDS if not fields.get(key)]
        if missing:
            raise ValueError(f"配置「{name}」缺少必填项：{'、'.join(missing)}。")
        store["active"] = name
        _write_store(store)
        write_env_values(fields)
        return list_profiles()


def delete_profile(name: str) -> dict:
    name = validate_profile_name(name)
    with _write_lock:
        store = _read_store()
        if name not in store["profiles"]:
            raise KeyError(name)
        del store["profiles"][name]
        if store["active"] == name:
            # 只清空激活标记，不动 .env：贸然清掉正在生效的配置会让工作台立刻不可用。
            store["active"] = ""
        _write_store(store)
        return list_profiles()


def profile_secret(name: str) -> dict[str, str]:
    """取某套配置的明文（仅供服务端自用，如连通性测试）。"""
    store = _read_store()
    fields = store["profiles"].get(name)
    if fields is None:
        raise KeyError(name)
    return dict(fields)
