import json

import httpx
import pytest

from story2script.llm_client import LLMClient


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def clear_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_TIMEOUT_SECONDS", raising=False)


def test_llm_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(lambda request: None)))

    with pytest.raises(ValueError, match="AI_API_KEY"):
        client.complete_json("prompt")


def test_llm_client_calls_openai_compatible_chat_api(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    content = client.complete_json("把小说改成剧本")

    assert content == '{"ok": true}'
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "把小说改成剧本"}]
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_llm_client_reads_dotenv_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clear_ai(monkeypatch)
    monkeypatch.delenv("STORY2SCRIPT_DISABLE_DOTENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AI_API_KEY=dotenv-key",
                "AI_BASE_URL=https://dotenv.test/v1/",
                "AI_MODEL=dotenv-model",
                "AI_TIMEOUT_SECONDS=45",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    client = LLMClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        env_file=env_file,
    )

    content = client.complete_json("prompt")

    assert content == '{"ok": true}'
    assert client.timeout_seconds == 45
    assert captured["url"] == "https://dotenv.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer dotenv-key"
    assert captured["payload"]["model"] == "dotenv-model"


def test_llm_client_environment_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clear_ai(monkeypatch)
    monkeypatch.delenv("STORY2SCRIPT_DISABLE_DOTENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AI_API_KEY=dotenv-key",
                "AI_BASE_URL=https://dotenv.test/v1",
                "AI_MODEL=dotenv-model",
                "AI_TIMEOUT_SECONDS=45",
            ]
        ),
        encoding="utf-8",
    )
    configure_ai(monkeypatch)

    client = LLMClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: None)),
        env_file=env_file,
    )

    assert client.api_key == "test-key"
    assert client.base_url == "https://example.test/v1"
    assert client.model == "test-model"
    assert client.timeout_seconds == 45


def test_llm_client_rejects_empty_model_response(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="empty response"):
        client.complete_json("prompt")


def test_llm_client_wraps_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="network error"):
        client.complete_json("prompt")


def test_llm_client_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"error": "model unavailable"})

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="HTTP 500"):
        client.complete_json("prompt")


def test_llm_client_wraps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="timed out"):
        client.complete_json("prompt")
