import json

import httpx
import pytest

from story2script.converter import AIConverter
from story2script.parser import parse_chapters

# 含可识别人物“林澈”的三章小说，使本地 global_state 生成 character-1。
NOVEL = (
    "第一章 开场\n林澈说：“出发吧。”\n"
    "第二章 线索\n林澈在码头等待。\n"
    "第三章 收束\n林澈回头看了一眼。"
)
NOVEL_CHAPTERS = parse_chapters(NOVEL)


def scene_dict(**overrides) -> dict:
    scene = {
        "id": "scene-1",
        "heading": "INT. 走廊 - NIGHT",
        "int_ext": "INT.",
        "time_of_day": "NIGHT",
        "location": "走廊",
        "source_chapter": "第一章 开场",
        "summary": "林澈察觉姐姐失踪并非意外。",
        "goal": "林澈试图确认真相。",
        "conflict": "新的线索推翻了意外结论。",
        "beat": "情节转折",
        "subtext": "林澈表面冷静，内心已经恐惧。",
        "characters": ["character-1"],
        "characters_present": ["character-1"],
        "props": [],
        "dramatization_decisions": [
            {
                "source_text": "林澈停下脚步。",
                "target": "action",
                "rendering": "林澈停下脚步，缓缓回头。",
                "reason": "",
            }
        ],
        "elements": [
            {"type": "action", "text": "林澈停下脚步，缓缓回头。"},
            {
                "type": "dialogue",
                "character": "character-1",
                "emotion": "紧张",
                "text": "不对……这不是意外。",
            },
        ],
        "camera_hints": ["近景：林澈绷紧的表情。"],
    }
    scene.update(overrides)
    return scene


def chapter_response(scenes: list[dict]) -> httpx.Response:
    content = json.dumps({"scenes": scenes}, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def test_ai_converter_requires_manual_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(lambda request: None)))

    with pytest.raises(ValueError, match="AI_API_KEY"):
        converter.convert(parse_chapters(NOVEL))


def test_ai_converter_uses_openai_compatible_chat_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, title="智能改编", genre="悬疑", adaptation_type="短剧")

    prompt = captured["payload"]["messages"][0]["content"]
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "test-model"
    assert "只把【本章】" in prompt
    assert "改编类型：短剧" in prompt
    assert "强反转" in prompt
    assert "全局状态表" in prompt
    assert "稳定人物表" in prompt
    assert "dramatization_decisions" in prompt
    assert screenplay.adaptation_type == "短剧"
    assert screenplay.title == "智能改编"
    assert screenplay.genre == "悬疑"
    assert screenplay.global_state.timeline[0].chapter == "第一章 开场"
    assert screenplay.scenes[0].int_ext == "INT."
    assert screenplay.scenes[0].camera_hints == ["近景：林澈绷紧的表情。"]


def test_ai_converter_merges_chapter_scenes_and_renumbers(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")

    # 每章一场，合并后顺序重排 id，并按各自章节回填 source_chapter。
    assert [scene.id for scene in screenplay.scenes] == ["scene-1", "scene-2", "scene-3"]
    assert [scene.source_chapter for scene in screenplay.scenes] == [
        "第一章 开场",
        "第二章 线索",
        "第三章 收束",
    ]


def test_ai_converter_splits_long_chapters_into_bounded_chunk_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        prompts.append(payload["messages"][0]["content"])
        return chapter_response([scene_dict(summary=f"片段 {len(prompts)}")])

    long_paragraphs = [
        f"林澈在走廊停下，记录第 {index} 个线索。"
        for index in range(120)
    ]
    long_novel = (
        "第一章 长夜\n"
        + "\n\n".join(long_paragraphs)
        + "\n第二章 线索\n林澈在码头等待。\n"
        "第三章 收束\n林澈回头看了一眼。"
    )

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(parse_chapters(long_novel), adaptation_type="短剧")

    assert len(prompts) > 3
    assert "本章片段：第 1/" in prompts[0]
    assert all("本章片段原文" in prompt for prompt in prompts)
    assert [scene.id for scene in screenplay.scenes] == [
        f"scene-{index}" for index in range(1, len(prompts) + 1)
    ]


def test_ai_converter_builds_characters_from_global_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")

    assert screenplay.characters[0].id == "character-1"
    assert screenplay.characters[0].name == "林澈"
    assert screenplay.source.chapter_count == 3


def test_ai_converter_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="不是有效 JSON"):
        converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")


def test_ai_converter_reports_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": "{\"scenes\": ["}}]},
        )

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="被截断"):
        converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")


def test_ai_converter_backfills_missing_scene_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = scene_dict()
    del scene["conflict"]

    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")

    assert screenplay.scenes[0].id == "scene-1"
    assert "待补充" in screenplay.scenes[0].conflict


def test_ai_converter_normalizes_imperfect_scene_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = scene_dict(
        int_ext="内景",
        time_of_day="夜晚",
        heading="走廊夜戏",
        characters=["林澈"],
        elements=[{"type": "dialogue", "character": "林澈", "text": "不对……这不是意外。"}],
    )
    del scene["id"]
    del scene["characters_present"]
    scene["duration"] = "2 分钟"  # Schema 不允许的多余字段

    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
    result_scene = screenplay.scenes[0]

    assert result_scene.id == "scene-1"
    assert result_scene.int_ext == "INT."
    assert result_scene.time_of_day == "NIGHT"
    assert result_scene.heading == "INT. 走廊 - NIGHT"
    # 用人物名引用的对白被映射回稳定 id。
    assert result_scene.characters == ["character-1"]
    assert result_scene.elements[0].character == "character-1"


def test_ai_converter_does_not_leak_placeholder_text(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = scene_dict()
    del scene["summary"]
    scene["elements"] = []
    scene["camera_hints"] = ["俯拍林澈蹲下捡信，特写信纸上的字。"]
    scene["dramatization_decisions"] = [{"target": "scene_description"}, {"target": "dialogue"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
    scene_result = screenplay.scenes[0]
    blob = json.dumps(screenplay.model_dump(mode="json"), ensure_ascii=False)

    assert "待补充摘要" not in blob
    assert "俯拍林澈" in scene_result.summary
    assert all(decision.rendering for decision in scene_result.dramatization_decisions)


def test_ai_converter_prompt_requires_preserving_dialogue(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
    prompt = captured["payload"]["messages"][0]["content"]

    assert "不能把对白只写进 dramatization_decisions" in prompt
    assert "camera_hints 只放简短镜头" in prompt


def test_ai_converter_rejects_when_no_scenes(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="没有返回任何有效场景"):
        converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
