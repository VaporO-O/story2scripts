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


_CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def loads_json_object(content: str) -> object:
    """Parse a JSON value from an LLM response, tolerating common wrappers.

    Models frequently wrap the JSON in Markdown ``` fences or add a sentence
    before/after it, which makes a bare ``json.loads`` fail even though the
    payload is valid. This tries the raw text, then a fenced block, then the
    outermost ``{...}`` / ``[...]`` span before giving up.
    """
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _CODE_FENCE_PATTERN.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError("无法从模型响应中解析出 JSON。")


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

    def complete_json(self, prompt: str, temperature: float = 0.3) -> str:
        """Return the model's JSON text response for a single user prompt."""
        self._ensure_configured()

        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
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
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"{self.usage_label} returned malformed response.") from exc

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
