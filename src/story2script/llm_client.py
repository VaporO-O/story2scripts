import json
import os

import httpx


class LLMClient:
    """Shared OpenAI-compatible chat completions client."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        usage_label: str = "AI mode",
    ) -> None:
        self.usage_label = usage_label
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    @property
    def api_key(self) -> str:
        return os.getenv("AI_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.getenv("AI_BASE_URL", "").strip().rstrip("/")

    @property
    def model(self) -> str:
        return os.getenv("AI_MODEL", "").strip()

    @property
    def timeout_seconds(self) -> float:
        raw_value = os.getenv("AI_TIMEOUT_SECONDS", "120").strip()
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
