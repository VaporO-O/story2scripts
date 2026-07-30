import json

import httpx
import pytest

from story2script.converter import AIConverter
from story2script.llm_cache import LLMResponseCache, llm_cache
from story2script.llm_client import LLMClient
from story2script.metrics import metrics
from story2script.parser import parse_chapters
from story2script.scene_rewrite import rewrite_scene
from story2script.converter import DemoConverter

NOVEL = (
    "第一章 开场\n林澈说：“出发吧。”\n"
    "第二章 线索\n林澈在码头等待。\n"
    "第三章 收束\n林澈回头看了一眼。"
)


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def chat_response(payload: object) -> httpx.Response:
    content = json.dumps(payload, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def counting_client(response_factory):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return response_factory(request, calls["count"])

    return LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler))), calls


# ---------------------------------------------------------------- 缓存单元


def test_cache_lru_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_LLM_CACHE_MAX_ENTRIES", "2")
    cache = LLMResponseCache()
    cache.put("a", "1")
    cache.put("b", "2")
    cache.put("c", "3")

    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.get("c") == "3"
    assert cache.stats()["entries"] == 2


def test_cache_disable_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_LLM_CACHE_DISABLE", "1")
    cache = LLMResponseCache()
    cache.put("a", "1")

    assert cache.get("a") is None
    assert cache.stats()["entries"] == 0


def test_cache_disk_layer(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("STORY2SCRIPT_LLM_CACHE_DIR", str(tmp_path / "cache"))
    cache = LLMResponseCache()
    cache.put("key1", "缓存内容")

    assert list((tmp_path / "cache").glob("*.json"))
    cache.clear()
    assert cache.get("key1") == "缓存内容"  # 内存 miss 后从磁盘回源


# ---------------------------------------------------------------- complete_json


def test_complete_json_cache_hit_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    client, calls = counting_client(lambda request, count: chat_response({"ok": count}))

    first = client.complete_json("同一个提示词")
    second = client.complete_json("同一个提示词")

    assert calls["count"] == 1
    assert first == second
    row = metrics.summary()["llm"]["AI mode"]
    assert row["calls"] == 2
    assert row["cache_hits"] == 1
    cached_event = next(
        event for event in metrics.recent_events() if event.get("cached")
    )
    assert cached_event["duration_ms"] == 0
    assert metrics.summary()["llm_overall"]["cache_hits"] == 1


def test_complete_json_use_cache_false_bypasses(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    client, calls = counting_client(lambda request, count: chat_response({"ok": count}))

    client.complete_json("提示词", use_cache=False)
    client.complete_json("提示词", use_cache=False)

    assert calls["count"] == 2


def test_failed_responses_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)

    def factory(request: httpx.Request, count: int) -> httpx.Response:
        if count == 1:
            return httpx.Response(500)
        return chat_response({"ok": True})

    client, calls = counting_client(factory)

    with pytest.raises(ValueError, match="HTTP 500"):
        client.complete_json("提示词")
    assert client.complete_json("提示词") == json.dumps({"ok": True}, ensure_ascii=False)
    assert calls["count"] == 2

    # 缓存里现在是成功响应，第三次调用命中
    client.complete_json("提示词")
    assert calls["count"] == 2


def test_different_temperature_uses_different_key(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    client, calls = counting_client(lambda request, count: chat_response({"n": count}))

    client.complete_json("提示词", temperature=0.3)
    client.complete_json("提示词", temperature=0.7)

    assert calls["count"] == 2


# ---------------------------------------------------------------- 业务绕过


def scene_payload(converter_scene: dict) -> dict:
    return {"scenes": [converter_scene]}


def test_converter_retry_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_ai_converter import scene_dict

    configure_ai(monkeypatch)
    chunk_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        content = payload["messages"][0]["content"]
        if "本章片段原文" not in content:
            return chat_response([])  # 人物小传请求
        chunk_calls["count"] += 1
        if chunk_calls["count"] == 1:
            return chat_response({"scenes": "不是列表"})  # HTTP 200 但内容无效
        return chat_response(scene_payload(scene_dict()))

    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters(NOVEL)
    screenplay = converter.convert(chapters[:1] + chapters[1:])

    # 单章单分块场景下：坏响应 1 次 + 重试真实请求（未吃缓存）
    assert chunk_calls["count"] >= 2
    assert screenplay.scenes


def test_scene_rewrite_ai_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    screenplay = DemoConverter().convert(parse_chapters(NOVEL), title="测试", genre="剧情")
    rewrite_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        rewrite_calls["count"] += 1
        replacement = screenplay.scenes[0].model_dump(mode="json")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(replacement, ensure_ascii=False)}}
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rewrite_scene(screenplay, "scene-1", "strengthen_conflict", mode="ai", client=client)
    rewrite_scene(screenplay, "scene-1", "strengthen_conflict", mode="ai", client=client)

    assert rewrite_calls["count"] == 2  # 重新生成语义：同请求不复用缓存


# ---------------------------------------------------------------- embed


def embed_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content.decode("utf-8"))
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": index, "embedding": [float(len(text)), 1.0]}
                for index, text in enumerate(payload["input"])
            ],
            "usage": {"prompt_tokens": 3},
        },
    )


def test_embed_per_text_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    monkeypatch.setenv("AI_EMBED_MODEL", "test-embed")
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        requests.append(payload["input"])
        return embed_response(request)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    first = client.embed(["一", "二", "三"])
    assert len(requests) == 1

    second = client.embed(["一", "二", "三"])
    assert len(requests) == 1  # 全命中，零请求
    assert second == first

    third = client.embed(["一", "四"])
    assert len(requests) == 2
    assert requests[1] == ["四"]  # 只有新增文本出网
    assert third[0] == first[0]

    llm_cache.clear()
    client.embed(["一"], use_cache=False)
    assert len(requests) == 3
