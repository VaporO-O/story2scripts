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
    assert "顶层 characters 的每个对象只能包含 id, name, description, motivation, arc" in captured[
        "payload"
    ]["messages"][0]["content"]
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


def test_ai_converter_prunes_extra_top_level_character_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = json.loads(valid_screenplay_json())
    data["characters"][0]["aliases"] = ["林澈"]
    data["characters"][0]["first_appearance"] = "第一章"
    data["characters"][0]["appearance_chapters"] = ["第一章"]
    data["characters"][0]["traits"] = ["敏锐"]
    data["characters"][0]["goal"] = "追查真相"
    data["characters"][0]["consistency_note"] = "这些字段只属于 global_state.characters。"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(data, ensure_ascii=False),
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
    character_data = screenplay.characters[0].model_dump(mode="json")

    assert character_data == {
        "id": "character-1",
        "name": "林澈",
        "description": "追查姐姐失踪真相的人。",
        "motivation": "找到姐姐失踪真相。",
        "arc": "从怀疑到主动面对真相。",
    }


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


def test_ai_converter_backfills_missing_scene_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired_data = json.loads(valid_screenplay_json())
    del repaired_data["scenes"][0]["conflict"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(repaired_data, ensure_ascii=False),
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

    # 缺失的剧本内容字段会被补成可编辑的占位文本，而不是直接 422。
    screenplay = converter.convert(chapters, adaptation_type="短剧")

    assert screenplay.scenes[0].id == "scene-1"
    assert screenplay.scenes[0].conflict
    assert "待补充" in screenplay.scenes[0].conflict


def test_ai_converter_backfills_missing_adaptation_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired_data = json.loads(valid_screenplay_json())
    del repaired_data["adaptation_type"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(repaired_data, ensure_ascii=False),
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

    # 模型遗漏 adaptation_type 时，按请求的改编类型回填。
    screenplay = converter.convert(chapters, adaptation_type="短剧")

    assert screenplay.adaptation_type == "短剧"


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


def test_ai_converter_normalizes_imperfect_scene_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imperfect_data = json.loads(valid_screenplay_json())
    scene = imperfect_data["scenes"][0]
    del scene["id"]  # 模型常见遗漏：缺 scene.id
    scene["int_ext"] = "内景"  # 中文写法
    scene["time_of_day"] = "夜晚"  # 中文写法
    scene["heading"] = "走廊夜戏"  # 与 slug line 不一致
    scene["duration"] = "2 分钟"  # Schema 不允许的多余字段
    del scene["characters_present"]  # 缺可选列表字段
    scene["characters"] = ["林澈"]  # 用人物名而非 id 引用
    scene["elements"] = [
        {"type": "dialogue", "character": "林澈", "text": "不对……这不是意外。"}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(imperfect_data, ensure_ascii=False),
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
    result_scene = screenplay.scenes[0]

    assert result_scene.id == "scene-1"
    assert result_scene.int_ext == "INT."
    assert result_scene.time_of_day == "NIGHT"
    assert result_scene.heading == "INT. 走廊 - NIGHT"
    # 用人物名引用的对白被映射回稳定 id
    assert result_scene.characters == ["character-1"]
    assert result_scene.elements[0].character == "character-1"


def test_ai_converter_does_not_leak_placeholder_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sparse_data = json.loads(valid_screenplay_json())
    scene = sparse_data["scenes"][0]
    # 复现线上情况：正文只放进 camera_hints，缺 summary、缺 elements，
    # dramatization_decisions 是没有 source_text/rendering 的空残桩。
    del scene["summary"]
    scene["elements"] = []
    scene["camera_hints"] = ["俯拍林澈蹲下捡信，特写信纸上的字。"]
    scene["dramatization_decisions"] = [{"target": "scene_description"}, {"target": "dialogue"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(sparse_data, ensure_ascii=False),
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
    blob = json.dumps(screenplay.model_dump(mode="json"), ensure_ascii=False)
    scene = screenplay.scenes[0]

    # 不允许把工具元注释或占位摘要写进剧本正文。
    assert "由 AI 输出补全" not in blob
    assert "待补充摘要" not in blob
    # summary 与决策从真实内容派生。
    assert "俯拍林澈" in scene.summary
    assert all(decision.rendering for decision in scene.dramatization_decisions)
    assert all("由 AI" not in decision.reason for decision in scene.dramatization_decisions)


def test_ai_converter_prompt_requires_preserving_dialogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_screenplay_json()}}]},
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    chapters = parse_chapters("第一章\n内容\n第二章\n内容\n第三章\n内容")

    converter.convert(chapters, adaptation_type="短剧")
    prompt = captured["payload"]["messages"][0]["content"]

    assert "原文里出现的台词必须原句保留" in prompt
    assert "camera_hints 只放简短镜头" in prompt


def test_ai_converter_rejects_when_no_scenes(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_data = json.loads(valid_screenplay_json())
    empty_data["scenes"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(empty_data, ensure_ascii=False),
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

    with pytest.raises(ValueError, match="没有返回任何有效场景"):
        converter.convert(chapters, adaptation_type="短剧")
