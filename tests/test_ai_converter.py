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
                    "int_ext": "INT.",
                    "time_of_day": "NIGHT",
                    "location": "走廊",
                    "source_chapter": "第一章",
                    "summary": "林澈察觉姐姐失踪并非意外。",
                    "goal": "林澈试图确认姐姐失踪的真实原因。",
                    "conflict": "新的线索推翻了意外结论。",
                    "beat": "情节转折",
                    "subtext": "林澈表面冷静，实际已经开始恐惧真相。",
                    "characters": ["character-1"],
                    "characters_present": ["character-1"],
                    "props": [],
                    "dramatization_decisions": [
                        {
                            "source_text": "林澈察觉姐姐失踪并非意外。",
                            "target": "subtext",
                            "rendering": "林澈表面冷静，实际已经开始恐惧真相。",
                            "reason": "心理判断用潜台词间接表现。",
                        },
                        {
                            "source_text": "林澈停下脚步，缓缓回头。",
                            "target": "action",
                            "rendering": "林澈停下脚步，缓缓回头。",
                            "reason": "可见身体反应转成动作行。",
                        },
                    ],
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
    assert "完整的 Story2Script Screenplay JSON 对象" in captured["payload"]["messages"][0][
        "content"
    ]
    assert "json.loads -> Screenplay.model_validate -> screenplay_to_yaml" in captured[
        "payload"
    ]["messages"][0]["content"]
    assert 'schema_version 必须固定为字符串 "1.0"' in captured["payload"]["messages"][0][
        "content"
    ]
    assert "source 会由后端根据章节解析结果回填为对象" in captured["payload"]["messages"][0][
        "content"
    ]
    assert "心理描写" in captured["payload"]["messages"][0]["content"]
    assert "goal, conflict, beat, subtext" in captured["payload"]["messages"][0]["content"]
    assert "改编类型：短剧" in captured["payload"]["messages"][0]["content"]
    assert "强反转" in captured["payload"]["messages"][0]["content"]
    assert "全局状态表" in captured["payload"]["messages"][0]["content"]
    assert "int_ext, time_of_day, location, characters_present, props" in captured["payload"][
        "messages"
    ][0]["content"]
    assert "dramatization_decisions" in captured["payload"]["messages"][0]["content"]
    assert "心理活动、情绪判断和未说出口的意图转 subtext" in captured["payload"]["messages"][
        0
    ]["content"]
    assert screenplay.adaptation_type == "短剧"
    assert screenplay.global_state.timeline[0].chapter == "第一章"
    assert screenplay.scenes[0].int_ext == "INT."
    assert screenplay.scenes[0].dramatization_decisions[0].target == "subtext"
    assert screenplay.scenes[0].camera_hints == ["近景：林澈绷紧的表情。"]


def test_ai_converter_backfills_source_from_parsed_chapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_data = json.loads(valid_screenplay_json())
    invalid_data["source"] = "模型误把来源章节写成字符串"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(invalid_data, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    screenplay = converter.convert(chapters, adaptation_type="短剧")

    assert screenplay.source.chapter_count == 3
    assert screenplay.source.chapter_titles == ["第一章", "第二章", "第三章"]


def test_ai_converter_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json"}}]},
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    with pytest.raises(ValueError, match="不是有效 JSON"):
        converter.convert(chapters, adaptation_type="短剧")


def test_ai_converter_rejects_schema_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_data = json.loads(valid_screenplay_json())
    del invalid_data["scenes"][0]["conflict"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(invalid_data, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    with pytest.raises(ValueError, match="不符合 Screenplay Schema"):
        converter.convert(chapters, adaptation_type="短剧")


def test_ai_converter_rejects_missing_adaptation_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_data = json.loads(valid_screenplay_json())
    del invalid_data["adaptation_type"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(invalid_data, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    with pytest.raises(ValueError, match="adaptation_type"):
        converter.convert(chapters, adaptation_type="短剧")


def test_ai_converter_rejects_adaptation_type_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatch_data = json.loads(valid_screenplay_json())
    mismatch_data["adaptation_type"] = "影视剧"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(mismatch_data, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    with pytest.raises(ValueError, match="adaptation_type"):
        converter.convert(chapters, adaptation_type="短剧")
