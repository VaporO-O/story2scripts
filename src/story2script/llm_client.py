import json
import os
import re
from pathlib import Path

import httpx


DOTENV_FILENAME = ".env"
DOTENV_DISABLE_ENV = "STORY2SCRIPT_DISABLE_DOTENV"
DOTENV_DISABLED_VALUES = {"1", "true", "yes", "on"}


def _default_env_path() -> Path:
    return Path.cwd() / DOTENV_FILENAME


def _dotenv_is_disabled() -> bool:
    return os.getenv(DOTENV_DISABLE_ENV, "").strip().lower() in DOTENV_DISABLED_VALUES


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").strip()
    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None

    value = value.strip()
    if value and value[0] not in {"'", '"'}:
        value = value.split(" #", 1)[0].strip()
    return key, _unquote_env_value(value)


def _load_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"Unable to read .env file: {exc}") from exc

    values: dict[str, str] = {}
    for line in lines:
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


_CODE_FENCE_PATTERN = re.compile(r"```(?:json|json5)?\s*(.*?)```", re.DOTALL)
_THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA_PATTERN = re.compile(r",(\s*[}\]])")


def _try_json(candidate: str) -> tuple[bool, object]:
    candidate = candidate.strip()
    if not candidate:
        return False, None
    try:
        return True, json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # 容忍尾随逗号（LLM 常见错误，如 [1, 2,] / {"a": 1,}）。
    repaired = _TRAILING_COMMA_PATTERN.sub(r"\1", candidate)
    if repaired != candidate:
        try:
            return True, json.loads(repaired)
        except json.JSONDecodeError:
            pass
    return False, None


def loads_json_object(content: str) -> object:
    """Parse a JSON value from an LLM response, tolerating common wrappers.

    Models frequently wrap the JSON in Markdown ``` fences, prepend reasoning /
    ``<think>`` blocks, add a sentence before/after it, or leave a trailing
    comma, any of which makes a bare ``json.loads`` fail even though a valid
    object is present. This strips those wrappers, then tries the raw text, a
    fenced block, and finally the outermost ``{...}`` / ``[...]`` span. On total
    failure it raises with a preview of the raw response so the real cause
    (e.g. truncated output, prose instead of JSON) is visible.
    """
    text = content.strip().lstrip("﻿").strip()
    text = _THINK_BLOCK_PATTERN.sub("", text).strip()

    candidates: list[str] = [text]
    fenced = _CODE_FENCE_PATTERN.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        ok, value = _try_json(candidate)
        if ok:
            return value

    preview = text[:500].replace("\n", "\\n") or "(空响应)"
    raise ValueError(f"无法从模型响应中解析出 JSON（原始响应前 500 字：{preview}）")


class LLMClient:
    """Shared OpenAI-compatible chat completions client."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        usage_label: str = "AI mode",
        env_file: str | Path | None = None,
        load_dotenv: bool = True,
    ) -> None:
        self.usage_label = usage_label
        self.env_file = Path(env_file) if env_file is not None else _default_env_path()
        self.env_values = (
            _load_env_file(self.env_file) if load_dotenv and not _dotenv_is_disabled() else {}
        )
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    @property
    def api_key(self) -> str:
        return self._config_value("AI_API_KEY")

    @property
    def base_url(self) -> str:
        return self._config_value("AI_BASE_URL").rstrip("/")

    @property
    def model(self) -> str:
        return self._config_value("AI_MODEL")

    @property
    def timeout_seconds(self) -> float:
        raw_value = self._config_value("AI_TIMEOUT_SECONDS", "120")
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{self.usage_label} requires numeric AI_TIMEOUT_SECONDS.") from exc

    @property
    def max_tokens(self) -> int | None:
        raw_value = self._config_value("AI_MAX_TOKENS")
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{self.usage_label} requires integer AI_MAX_TOKENS.") from exc

    def complete_json(self, prompt: str, temperature: float = 0.3) -> str:
        """Return the model's JSON text response for a single user prompt."""
        self._ensure_configured()

        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        # 长剧本 JSON 可能超出服务商默认输出上限被截断；配置 AI_MAX_TOKENS 可显式调高。
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ValueError(f"{self.usage_label} request timed out.") from exc
        except httpx.RequestError as exc:
            raise ValueError(f"{self.usage_label} network error: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise ValueError(f"{self.usage_label} request failed with HTTP {status_code}.") from exc

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"{self.usage_label} returned invalid JSON response.") from exc

        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"{self.usage_label} returned malformed response.") from exc

        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if finish_reason == "length":
            raise ValueError(
                f"{self.usage_label} 输出被截断（finish_reason=length）：本次返回超出模型单次输出"
                "长度上限。请在 .env 调高 AI_MAX_TOKENS（如 16384），或减少单次转换的章节数量。"
            )

        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{self.usage_label} returned empty response.")

        return content

    def _ensure_configured(self) -> None:
        if not self.api_key:
            raise ValueError(f"{self.usage_label} requires AI_API_KEY.")
        if not self.base_url:
            raise ValueError(f"{self.usage_label} requires AI_BASE_URL.")
        if not self.model:
            raise ValueError(f"{self.usage_label} requires AI_MODEL.")

    def _config_value(self, name: str, default: str = "") -> str:
        env_value = os.getenv(name)
        if env_value is not None:
            return env_value.strip()
        return self.env_values.get(name, default).strip()
