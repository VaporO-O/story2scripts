import json

import httpx
import pytest

from story2script.converter import AIConverter
from story2script.parser import parse_chapters


def valid_screenplay_json() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "title": "智能改编",
            "genre": "悬疑",
            "adaptation_type": "短剧",
            "logline": "林澈意识到姐姐失踪并非意外。",
            "source": {
                "chapter_count": 3,
                "chapter_titles": ["第一章", "第二章", "第三章"],
            },
            "global_state": {
                "characters": [],
                "locations": [],
                "timeline": [],
            },
            "characters": [
                {
                    "id": "character-1",
                    "name": "林澈",
                    "description": "追查姐姐失踪真相的人。",
                    "motivation": "找到姐姐失踪真相。",
                    "arc": "从怀疑到主动面对真相。",
                }
            ],
            "scenes": [
                {
                    "id": "scene-1",
                    "heading": "INT. 走廊 - NIGHT",
                    "source_chapter": "第一章",
                    "summary": "林澈察觉姐姐失踪并非意外。",
                    "goal": "林澈试图确认姐姐失踪的真实原因。",
                    "conflict": "新的线索推翻了意外结论。",
                    "beat": "情节转折",
                    "subtext": "林澈表面冷静，实际已经开始恐惧真相。",
                    "characters": ["character-1"],
                    "elements": [
                        {"type": "action", "text": "林澈停下脚步，缓缓回头。"},
                        {
                            "type": "dialogue",
                            "character": "character-1",
                            "parenthetical": "",
                            "emotion": "紧张",
                            "text": "不对……这不是意外。",
                        },
                    ],
                    "camera_hints": ["近景：林澈绷紧的表情。"],
                }
            ],
        },
        ensure_ascii=False,
    )


def test_ai_converter_requires_manual_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(lambda request: None)))

    with pytest.raises(ValueError, match="AI_API_KEY"):
        converter.convert(parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容"))


def test_ai_converter_uses_openai_compatible_chat_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": valid_screenplay_json(),
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    converter = AIConverter(client=client)
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    screenplay = converter.convert(
        chapters,
        title="智能改编",
        genre="悬疑",
        adaptation_type="短剧",
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "test-model"
    assert "心理描写" in captured["payload"]["messages"][0]["content"]
    assert "goal, conflict, beat, subtext" in captured["payload"]["messages"][0]["content"]
    assert "改编类型：短剧" in captured["payload"]["messages"][0]["content"]
    assert "强反转" in captured["payload"]["messages"][0]["content"]
    assert "全局状态表" in captured["payload"]["messages"][0]["content"]
    assert screenplay.adaptation_type == "短剧"
    assert screenplay.global_state.timeline[0].chapter == "第一章"
    assert screenplay.scenes[0].camera_hints == ["近景：林澈绷紧的表情。"]
