import json
import os
import re
import time
from pathlib import Path

import httpx

from .llm_cache import cache_key, llm_cache
from .metrics import metrics
from .security import redact_secrets


DOTENV_FILENAME = ".env"
DOTENV_DISABLE_ENV = "STORY2SCRIPT_DISABLE_DOTENV"
DOTENV_DISABLED_VALUES = {"1", "true", "yes", "on"}
EMBEDDINGS_METRICS_LABEL = "AI embeddings"

# 值得退避重试的状态码：网关超时 / 限流 / 上游临时故障。
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _tagged_error(message: str, error_kind: str, status_code: int | None = None) -> ValueError:
    """带机器可读标签的业务错误。

    调用方（如分块转换的重试循环）据此判断该不该退避重试：401/400 这类请求本身
    有问题的错误重试多少次都一样，而 504/429 等一下往往就能恢复。
    """
    error = ValueError(message)
    error.error_kind = error_kind  # type: ignore[attr-defined]
    error.status_code = status_code  # type: ignore[attr-defined]
    return error


def is_fatal_error(exc: BaseException) -> bool:
    """重试是否注定无用。

    默认「可重试」——空响应、截断、偶发坏 JSON、网关超时都可能是瞬时问题。
    只有配置缺失和 4xx 客户端错误（401/403/404 等，排除 408/425/429）才立即放弃，
    免得白等两轮退避。
    """
    if getattr(exc, "error_kind", "") == "config":
        return True
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return status_code not in RETRYABLE_STATUS_CODES
    return False
CHAT_COMPLETIONS_WIRE_API = "chat_completions"
RESPONSES_WIRE_API = "responses"
WIRE_API_ALIASES = {
    "chat": CHAT_COMPLETIONS_WIRE_API,
    "chat/completions": CHAT_COMPLETIONS_WIRE_API,
    "chat_completions": CHAT_COMPLETIONS_WIRE_API,
    "responses": RESPONSES_WIRE_API,
}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


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

    preview = redact_secrets(text[:500].replace("\n", "\\n")) or "(空响应)"
    raise ValueError(f"无法从模型响应中解析出 JSON（原始响应前 500 字：{preview}）")


class LLMClient:
    """Shared OpenAI-compatible Chat Completions and Responses client."""

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
    def wire_api(self) -> str:
        raw_value = self._config_value("AI_WIRE_API", CHAT_COMPLETIONS_WIRE_API).lower()
        wire_api = WIRE_API_ALIASES.get(raw_value)
        if wire_api is None:
            raise ValueError(
                f"{self.usage_label} requires AI_WIRE_API to be "
                f"{CHAT_COMPLETIONS_WIRE_API} or {RESPONSES_WIRE_API}."
            )
        return wire_api

    @property
    def reasoning_effort(self) -> str:
        return self._config_value("AI_REASONING_EFFORT")

    @property
    def disable_response_storage(self) -> bool:
        raw_value = self._config_value("AI_DISABLE_RESPONSE_STORAGE", "false").lower()
        if raw_value in TRUE_VALUES:
            return True
        if raw_value in FALSE_VALUES:
            return False
        raise ValueError(
            f"{self.usage_label} requires boolean AI_DISABLE_RESPONSE_STORAGE."
        )

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

    @property
    def max_concurrency(self) -> int:
        """How many chunk requests may run in parallel (AI_MAX_CONCURRENCY, default 4)."""
        raw_value = self._config_value("AI_MAX_CONCURRENCY", "4")
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{self.usage_label} requires integer AI_MAX_CONCURRENCY.") from exc
        return max(1, value)

    @property
    def embed_model(self) -> str:
        return self._config_value("AI_EMBED_MODEL")

    @property
    def embed_batch_size(self) -> int:
        raw_value = self._config_value("AI_EMBED_BATCH_SIZE", "16")
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{self.usage_label} requires integer AI_EMBED_BATCH_SIZE.") from exc
        return max(1, value)

    def complete_json(self, prompt: str, temperature: float = 0.3, use_cache: bool = True) -> str:
        """Return the model's JSON text response for a single user prompt.

        use_cache=False 用于"重新生成"语义的调用（场景重写）与分块转换的
        重试路径：同请求需要不同结果时不得复用缓存。
        """
        self._ensure_configured()
        request_url, body = self._generation_request(prompt, temperature)
        key = cache_key(
            "generate",
            self.wire_api,
            self.base_url,
            self.model,
            self.reasoning_effort,
            self.disable_response_storage,
            self.max_tokens,
            temperature,
            prompt,
        )
        if use_cache:
            cached = llm_cache.get(key)
            if isinstance(cached, str):
                metrics.record_llm_call(
                    label=self.usage_label,
                    model=self.model,
                    duration_ms=0,
                    ok=True,
                    prompt_chars=len(prompt),
                    response_chars=len(cached),
                    cached=True,
                )
                return cached

        started = time.perf_counter()
        error_kind = ""
        usage: dict = {}
        try:
            try:
                response = self.client.post(
                    request_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                error_kind = "timeout"
                raise _tagged_error(f"{self.usage_label} request timed out.", error_kind) from exc
            except httpx.RequestError as exc:
                error_kind = "network"
                raise _tagged_error(
                    f"{self.usage_label} network error: {redact_secrets(str(exc))}",
                    error_kind,
                ) from exc
            except httpx.HTTPStatusError as exc:
                error_kind = "http_status"
                status_code = exc.response.status_code
                raise _tagged_error(
                    f"{self.usage_label} request failed with HTTP {status_code}.",
                    error_kind,
                    status_code=status_code,
                ) from exc

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                error_kind = "invalid_json"
                raise ValueError(f"{self.usage_label} returned invalid JSON response.") from exc

            raw_usage = payload.get("usage") if isinstance(payload, dict) else None
            usage = raw_usage if isinstance(raw_usage, dict) else {}

            if self.wire_api == RESPONSES_WIRE_API:
                status = payload.get("status") if isinstance(payload, dict) else None
                incomplete_details = (
                    payload.get("incomplete_details") if isinstance(payload, dict) else None
                )
                incomplete_reason = (
                    incomplete_details.get("reason")
                    if isinstance(incomplete_details, dict)
                    else None
                )
                if status == "incomplete" and incomplete_reason == "max_output_tokens":
                    error_kind = "truncated"
                    raise ValueError(
                        f"{self.usage_label} 输出被截断（reason=max_output_tokens）：本次返回超出"
                        "模型单次输出长度上限。请在 .env 调高 AI_MAX_TOKENS（如 16384），"
                        "或减少单次转换的章节数量。"
                    )
                if status == "incomplete":
                    error_kind = "incomplete"
                    raise ValueError(
                        f"{self.usage_label} returned incomplete response"
                        f" ({incomplete_reason or 'unknown reason'})."
                    )
                if status == "failed":
                    error_kind = "failed"
                    raise ValueError(f"{self.usage_label} returned failed response.")
                content = self._responses_output_text(payload)
            else:
                try:
                    choice = payload["choices"][0]
                    content = choice["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    error_kind = "malformed"
                    raise ValueError(f"{self.usage_label} returned malformed response.") from exc

                finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                if finish_reason == "length":
                    error_kind = "truncated"
                    raise ValueError(
                        f"{self.usage_label} 输出被截断（finish_reason=length）：本次返回超出模型"
                        "单次输出长度上限。请在 .env 调高 AI_MAX_TOKENS（如 16384），"
                        "或减少单次转换的章节数量。"
                    )

            if not isinstance(content, str):
                error_kind = "malformed"
                raise ValueError(f"{self.usage_label} returned malformed response.")
            if not content.strip():
                error_kind = "empty"
                raise ValueError(f"{self.usage_label} returned empty response.")
        except ValueError:
            metrics.record_llm_call(
                label=self.usage_label,
                model=self.model,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error_kind=error_kind or "unknown",
                prompt_chars=len(prompt),
            )
            raise

        metrics.record_llm_call(
            label=self.usage_label,
            model=self.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=True,
            prompt_tokens=usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or usage.get("output_tokens") or 0,
            prompt_chars=len(prompt),
            response_chars=len(content),
        )
        if use_cache:
            llm_cache.put(key, content)
        return content

    def _generation_request(self, prompt: str, temperature: float) -> tuple[str, dict]:
        if self.wire_api == RESPONSES_WIRE_API:
            body: dict = {
                "model": self.model,
                "input": [{"role": "user", "content": prompt}],
                "text": {"format": {"type": "json_object"}},
            }
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
            if not self.reasoning_effort or self.reasoning_effort.lower() == "none":
                body["temperature"] = temperature
            if self.disable_response_storage:
                body["store"] = False
            if self.max_tokens is not None:
                body["max_output_tokens"] = self.max_tokens
            return f"{self.base_url}/responses", body

        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        return f"{self.base_url}/chat/completions", body

    @staticmethod
    def _responses_output_text(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None

        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text

        output = payload.get("output")
        if not isinstance(output, list):
            return None

        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") not in {"output_text", "text"}:
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        return "".join(text_parts)

    @property
    def retry_backoff_seconds(self) -> tuple[float, ...]:
        """分块重试之间的等待秒数（AI_RETRY_BACKOFF_SECONDS，默认 "1,3"）。

        瞬时失败（网关 504、限流 429）立刻重试往往仍然失败，还会加重上游负担；
        退避一到几秒的成功率明显更高。设为 "0" 可关闭等待。
        """
        raw_value = self._config_value("AI_RETRY_BACKOFF_SECONDS", "1,3")
        delays: list[float] = []
        for part in raw_value.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                delays.append(max(0.0, float(part)))
            except ValueError as exc:
                raise ValueError(
                    f"{self.usage_label} requires numeric AI_RETRY_BACKOFF_SECONDS "
                    '(comma-separated, e.g. "1,3").'
                ) from exc
        return tuple(delays)

    @property
    def chapter_chunk_chars(self) -> int:
        """单个分块的字符上限（AI_CHAPTER_CHUNK_CHARS，默认 1800）。

        调小可以缩短单次请求的输入与输出，是应对网关超时（504）最直接的手段；
        代价是分块数变多、总调用次数上升。
        """
        raw_value = self._config_value("AI_CHAPTER_CHUNK_CHARS", "1800")
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"{self.usage_label} requires integer AI_CHAPTER_CHUNK_CHARS."
            ) from exc
        return max(200, value)

    def _ensure_configured(self) -> None:
        # 配置缺失属于"重试也没用"，打上标签让分块重试循环立即放弃。
        if not self.api_key:
            raise _tagged_error(f"{self.usage_label} requires AI_API_KEY.", "config")
        if not self.base_url:
            raise _tagged_error(f"{self.usage_label} requires AI_BASE_URL.", "config")
        if not self.model:
            raise _tagged_error(f"{self.usage_label} requires AI_MODEL.", "config")

    def embed(self, texts: list[str], use_cache: bool = True) -> list[list[float]]:
        """Batch-embed texts via the OpenAI-compatible ``/embeddings`` endpoint.

        逐文本缓存：只把未命中的文本发给服务商（重建知识库时只需 embed 新增
        文本）。Calls are recorded in metrics under the fixed "AI embeddings"
        label so embedding cost stays a separate dimension from chat completions.
        """
        self._ensure_embed_configured()
        if not texts:
            return []

        keys = [
            cache_key("embed", self.base_url, self.embed_model, text) for text in texts
        ]
        results: list[list[float] | None] = [None] * len(texts)
        pending: list[int] = []
        if use_cache:
            hit_chars = 0
            for index, key in enumerate(keys):
                cached = llm_cache.get(key)
                if isinstance(cached, list) and cached:
                    results[index] = [float(value) for value in cached]
                    hit_chars += len(texts[index])
                else:
                    pending.append(index)
            if len(pending) < len(texts):
                metrics.record_llm_call(
                    label=EMBEDDINGS_METRICS_LABEL,
                    model=self.embed_model,
                    duration_ms=0,
                    ok=True,
                    prompt_chars=hit_chars,
                    cached=True,
                )
        else:
            pending = list(range(len(texts)))

        batch_size = self.embed_batch_size
        for start in range(0, len(pending), batch_size):
            index_batch = pending[start : start + batch_size]
            vectors = self._embed_batch([texts[index] for index in index_batch])
            for index, vector in zip(index_batch, vectors):
                results[index] = vector
                if use_cache:
                    llm_cache.put(keys[index], vector)
        return [vector for vector in results if vector is not None]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        started = time.perf_counter()
        error_kind = ""
        usage: dict = {}
        prompt_chars = sum(len(text) for text in batch)
        try:
            try:
                response = self.client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.embed_model, "input": batch},
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                error_kind = "timeout"
                raise ValueError(f"{self.usage_label} embeddings request timed out.") from exc
            except httpx.RequestError as exc:
                error_kind = "network"
                raise ValueError(
                    f"{self.usage_label} embeddings network error: {redact_secrets(str(exc))}"
                ) from exc
            except httpx.HTTPStatusError as exc:
                error_kind = "http_status"
                status_code = exc.response.status_code
                raise ValueError(
                    f"{self.usage_label} embeddings request failed with HTTP {status_code}."
                ) from exc

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                error_kind = "invalid_json"
                raise ValueError(
                    f"{self.usage_label} embeddings returned invalid JSON response."
                ) from exc

            raw_usage = payload.get("usage") if isinstance(payload, dict) else None
            usage = raw_usage if isinstance(raw_usage, dict) else {}

            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                error_kind = "malformed"
                raise ValueError(f"{self.usage_label} embeddings returned malformed response.")
            vectors: list[list[float] | None] = [None] * len(batch)
            for position, item in enumerate(data):
                embedding = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(embedding, list) or not embedding:
                    error_kind = "malformed"
                    raise ValueError(
                        f"{self.usage_label} embeddings returned malformed response."
                    )
                index = item.get("index")
                slot = index if isinstance(index, int) and 0 <= index < len(batch) else position
                vectors[slot] = [float(value) for value in embedding]
            if any(vector is None for vector in vectors):
                error_kind = "malformed"
                raise ValueError(f"{self.usage_label} embeddings returned malformed response.")
        except ValueError:
            metrics.record_llm_call(
                label=EMBEDDINGS_METRICS_LABEL,
                model=self.embed_model,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error_kind=error_kind or "unknown",
                prompt_chars=prompt_chars,
            )
            raise

        metrics.record_llm_call(
            label=EMBEDDINGS_METRICS_LABEL,
            model=self.embed_model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=True,
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
            prompt_chars=prompt_chars,
        )
        return [vector for vector in vectors if vector is not None]

    def _ensure_embed_configured(self) -> None:
        if not self.api_key:
            raise ValueError(f"{self.usage_label} requires AI_API_KEY.")
        if not self.base_url:
            raise ValueError(f"{self.usage_label} requires AI_BASE_URL.")
        if not self.embed_model:
            raise ValueError(f"{self.usage_label} requires AI_EMBED_MODEL.")

    def _config_value(self, name: str, default: str = "") -> str:
        env_value = os.getenv(name)
        if env_value is not None:
            return env_value.strip()
        return self.env_values.get(name, default).strip()
