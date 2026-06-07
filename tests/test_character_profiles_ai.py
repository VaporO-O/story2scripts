import json

import httpx
import pytest

from story2script.character_profiles_ai import (
    AICharacterProfiler,
    DemoCharacterProfiler,
    get_character_profiler,
)
from story2script.parser import parse_chapters

NOVEL = (
    "第1章 失踪\n"
    "林澈是林晚的弟弟。林澈说：“我会查下去。”林晚说：“别来找我。”\n"
    "第2章 线索\n林澈继续调查。\n"
    "第3章 追问\n林澈停在门口。"
)


def ai_profiles_json() -> str:
    return json.dumps(
        {
            "profiles": [
                {
                    "name": "林澈",
                    "role": "主角",
                    "personality": "执着、敏锐、克制",
                    "goal": "查清姐姐林晚失踪的真相。",
                    "relationships": ["是林晚的弟弟", "与隐藏的真凶对立"],
                    "key_change": "从逃避现实到主动直面真相。",
                },
                {
                    "name": "林晚",
                    "role": "关键配角",
                    "personality": "",
                    "goal": "留下线索指引弟弟。",
                    "relationships": [],
                    "key_change": "暂无明显转变",
                },
                {
                    "name": "保安老王",
                    "role": "路人",
                    "personality": "多疑",
                    "goal": "无关紧要。",
                    "relationships": [],
                    "key_change": "无",
                },
            ]
        },
        ensure_ascii=False,
    )


def test_get_character_profiler_returns_demo_by_default() -> None:
    assert isinstance(get_character_profiler(), DemoCharacterProfiler)
    assert isinstance(get_character_profiler("ai"), AICharacterProfiler)


def test_get_character_profiler_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="不支持的人物小传模式"):
        get_character_profiler("magic")


def test_ai_profiler_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    profiler = AICharacterProfiler(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: None))
    )

    with pytest.raises(ValueError, match="AI_API_KEY"):
        profiler.extract(parse_chapters(NOVEL))


def test_ai_profiler_enriches_local_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ai_profiles_json()}}]}
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    profiler = AICharacterProfiler(client=httpx.Client(transport=httpx.MockTransport(handler)))

    profiles = profiler.extract(parse_chapters(NOVEL))
    by_name = {profile["name"]: profile for profile in profiles}

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    prompt = captured["payload"]["messages"][0]["content"]
    assert "林澈" in prompt
    assert "personality" in prompt

    # 语义字段被 LLM 增强。
    assert by_name["林澈"]["personality"] == "执着、敏锐、克制"
    assert by_name["林澈"]["goal"] == "查清姐姐林晚失踪的真相。"
    assert by_name["林澈"]["relationships"] == ["是林晚的弟弟", "与隐藏的真凶对立"]
    assert by_name["林澈"]["key_change"] == "从逃避现实到主动直面真相。"
    assert by_name["林澈"]["role"] == "主角"

    # 出场章节保持本地权威值，不被 LLM 覆盖。
    assert by_name["林澈"]["appearance_chapters"] == ["第1章 失踪", "第2章 线索", "第3章 追问"]

    # LLM 留空的字段回退到本地结果。
    assert by_name["林晚"]["personality"] == "待作者进一步补充"

    # LLM 编造的、不在本地名单里的人物被忽略。
    assert "保安老王" not in by_name


def test_ai_profiler_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    profiler = AICharacterProfiler(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="人物小传"):
        profiler.extract(parse_chapters(NOVEL))
