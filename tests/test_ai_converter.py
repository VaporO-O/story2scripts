import json

import httpx
import pytest

import story2script.converter as converter_module
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
        payload = json.loads(request.content.decode("utf-8"))
        # 只抓片段转换请求，忽略并发的人物小传请求。
        if "本章片段原文" in payload["messages"][0]["content"]:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers["authorization"]
            captured["payload"] = payload
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
        content = payload["messages"][0]["content"]
        # 只统计片段转换请求，忽略并发的人物小传请求。
        if "本章片段原文" in content:
            prompts.append(content)
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
    # 片段现在可能并发调用，调用先后顺序不固定，因此不依赖 prompts[0]。
    assert any("本章片段：第 1/" in prompt for prompt in prompts)
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

    with pytest.raises(ValueError, match="解析出 JSON"):
        converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")


def test_ai_converter_retries_transient_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            # 首次返回空响应（瞬时问题），随后正常返回。
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")

    assert state["calls"] >= 2  # 进行了重试
    assert screenplay.scenes  # 重试后成功出稿


def test_ai_converter_skips_persistently_failing_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    novel = parse_chapters(
        "第一章 甲\n林澈说：“出发。”\n"
        "第二章 乙\n普通的开场叙述。\n这一段正文总是触发空响应ZZZQQQ需要跳过。\n"
        "第三章 丙\n林澈说：“收工。”"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "ZZZQQQ" in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(novel, adaptation_type="短剧")

    # 持续失败的“第二章 乙”被跳过，其它章节仍正常出稿，整篇不再前功尽弃。
    assert screenplay.scenes
    assert any(scene.source_chapter == "第一章 甲" for scene in screenplay.scenes)
    assert not any(scene.source_chapter == "第二章 乙" for scene in screenplay.scenes)


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


def test_ai_converter_normalizes_dialogue_alias_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = scene_dict(
        elements=[
            {
                "type": "台词",
                "speaker": "林澈",
                "line": "出发吧。",
                "emotion": "坚定",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
    dialogue = screenplay.scenes[0].elements[0]

    assert dialogue.type == "dialogue"
    assert dialogue.character == "character-1"
    assert dialogue.text == "出发吧。"
    assert dialogue.emotion == "坚定"


def test_ai_converter_splits_speaker_lines_from_action_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = scene_dict(
        elements=[
            {
                "type": "action",
                "text": "林澈停在门口。\n林澈：出发吧。\n林澈（压低声音）：别回头。",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return chapter_response([scene])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    screenplay = converter.convert(NOVEL_CHAPTERS, adaptation_type="短剧")
    elements = screenplay.scenes[0].elements
    dialogues = [element for element in elements if element.type == "dialogue"]

    assert elements[0].type == "action"
    assert len(dialogues) == 2
    assert [dialogue.text for dialogue in dialogues] == ["出发吧。", "别回头。"]
    assert all(dialogue.character == "character-1" for dialogue in dialogues)
    assert dialogues[1].parenthetical == "压低声音"


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
        payload = json.loads(request.content.decode("utf-8"))
        # 只抓片段转换请求，忽略并发的人物小传请求。
        if "本章片段原文" in payload["messages"][0]["content"]:
            captured["payload"] = payload
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


def test_compact_global_state_no_longer_carries_full_timeline() -> None:
    from story2script.converter import _compact_global_state
    from story2script.story_state import extract_global_story_state

    compact = _compact_global_state(extract_global_story_state(NOVEL_CHAPTERS))

    assert set(compact.keys()) == {"locations"}


def test_chunk_prompt_includes_retrieved_memo_without_future_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        content = payload["messages"][0]["content"]
        # 只抓片段转换请求，忽略并发的人物小传请求。
        if "本章片段原文" in content:
            prompts.append(content)
        return chapter_response([scene_dict()])

    configure_ai(monkeypatch)
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    converter.convert(NOVEL_CHAPTERS)

    assert prompts
    assert all("相关前文备忘" in prompt for prompt in prompts)
    assert all('"timeline"' not in prompt for prompt in prompts)

    first_prompt = next(prompt for prompt in prompts if "本章标题：第一章 开场" in prompt)
    memo_line = next(
        line for line in first_prompt.splitlines() if line.startswith("相关前文备忘")
    )
    assert memo_line.endswith("[]")

    second_prompt = next(prompt for prompt in prompts if "本章标题：第二章 线索" in prompt)
    memo_line = next(
        line for line in second_prompt.splitlines() if line.startswith("相关前文备忘")
    )
    assert "第一章 开场" in memo_line
    assert "回头看了一眼" not in memo_line  # 第三章内容不得泄漏进第二章的备忘


def _progress_handler(bad_chunk_index: int | None = None):
    """构造一个 handler，可让指定序号的分块首次返回坏数据以触发重试。"""
    state = {"chunk_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        state["chunk_calls"] += 1
        if bad_chunk_index is not None and state["chunk_calls"] == bad_chunk_index:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"scenes": "不是列表"}'}}]}
            )
        title = prompt.split("本章标题：")[1].split("\n")[0]
        return chapter_response([scene_dict(source_chapter=title)])

    return handler, state


def test_ai_converter_reports_chunk_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    handler, _ = _progress_handler()
    events: list[tuple[int, int, str]] = []

    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    converter.convert(
        NOVEL_CHAPTERS, progress_cb=lambda done, total, note: events.append((done, total, note))
    )

    assert events
    dones = [done for done, _, _ in events]
    totals = {total for _, total, _ in events}
    assert dones == sorted(dones)                       # 单调不减
    assert len(totals) == 1                             # 总数在整轮里稳定
    assert dones[-1] == events[-1][1]                   # 终值到达总数
    # 三段耗时都可见：建索引、逐段检索、逐段处理，以及收尾
    assert any("建立前文检索索引" in note for _, _, note in events)
    assert any("检索前文备忘" in note for _, _, note in events)
    assert any("已处理" in note for _, _, note in events)
    assert any("归一化" in note for _, _, note in events)


def test_ai_converter_reports_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    handler, _ = _progress_handler(bad_chunk_index=1)
    events: list[tuple[int, int, str]] = []

    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    screenplay = converter.convert(
        NOVEL_CHAPTERS, progress_cb=lambda done, total, note: events.append((done, total, note))
    )

    # 重试此前完全不可见（进度条卡住），现在应作为诊断信息出现
    assert any("次尝试" in note for _, _, note in events)
    assert screenplay.scenes


def test_ai_converter_reports_skipped_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    state = {"chunk_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        state["chunk_calls"] += 1
        # 第一章的片段始终失败，其余正常：整篇仍应出稿并汇报跳过数
        if "第一章" in prompt.split("本章标题：")[1].split("\n")[0]:
            return httpx.Response(500)
        title = prompt.split("本章标题：")[1].split("\n")[0]
        return chapter_response([scene_dict(source_chapter=title)])

    events: list[tuple[int, int, str]] = []
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    screenplay = converter.convert(
        NOVEL_CHAPTERS, progress_cb=lambda done, total, note: events.append((done, total, note))
    )

    assert screenplay.scenes                                  # 其余片段仍出稿
    assert any("失败已跳过" in note for _, _, note in events)
    # 失败的片段也计入"已处理"，进度不会停在中途
    assert events[-1][0] == events[-1][1]


# ---------------------------------------------------------------- 504 相关修复


def test_retry_uses_backoff_and_reports_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """瞬时失败（如网关 504）应退避后重试，而不是三次挤在同一瞬间。"""
    configure_ai(monkeypatch)
    monkeypatch.setenv("AI_RETRY_BACKOFF_SECONDS", "1,3")
    slept: list[float] = []
    monkeypatch.setattr(converter_module.time, "sleep", lambda seconds: slept.append(seconds))

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        attempts["count"] += 1
        if attempts["count"] <= 2:
            return httpx.Response(504)          # 网关超时：值得重试
        title = prompt.split("本章标题：")[1].split("\n")[0]
        return chapter_response([scene_dict(source_chapter=title)])

    events: list[str] = []
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    screenplay = converter.convert(
        parse_chapters("第一章 甲\n甲的正文。\n第二章 乙\n乙的正文。\n第三章 丙\n丙的正文。")[:1]
        + parse_chapters("第一章 甲\n甲的正文。\n第二章 乙\n乙的正文。\n第三章 丙\n丙的正文。")[1:],
        progress_cb=lambda done, total, note: events.append(note),
    )

    assert screenplay.scenes
    assert slept[:2] == [1.0, 3.0]                       # 两次退避按配置递增
    assert any("等待" in note and "重试" in note for note in events)


def test_fatal_error_skips_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 这类客户端错误重试也没用，应立即放弃而不是白等两轮退避。"""
    configure_ai(monkeypatch)
    monkeypatch.setenv("AI_RETRY_BACKOFF_SECONDS", "1,3")
    slept: list[float] = []
    monkeypatch.setattr(converter_module.time, "sleep", lambda seconds: slept.append(seconds))
    chunk_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        chunk_calls["count"] += 1
        return httpx.Response(401)

    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="AI 全文转换失败"):
        converter.convert(NOVEL_CHAPTERS)

    # 每个片段只尝试一次（共 3 章 → 3 个片段），且完全没有退避等待
    assert chunk_calls["count"] == 3
    assert slept == []


def test_chunk_char_limit_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """调小分块上限可缩短单次请求，是应对网关超时最直接的手段。"""
    configure_ai(monkeypatch)
    long_novel = (
        "第一章 甲\n" + "甲的正文。" * 200 + "\n"
        "第二章 乙\n" + "乙的正文。" * 200 + "\n"
        "第三章 丙\n" + "丙的正文。" * 200
    )
    chapters = parse_chapters(long_novel)

    def make_counter():
        seen = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
            if "本章片段原文" not in prompt:
                return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
            seen["count"] += 1
            title = prompt.split("本章标题：")[1].split("\n")[0]
            return chapter_response([scene_dict(source_chapter=title)])

        return handler, seen

    handler_wide, wide = make_counter()
    monkeypatch.setenv("AI_CHAPTER_CHUNK_CHARS", "1800")
    AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler_wide))).convert(chapters)

    handler_narrow, narrow = make_counter()
    monkeypatch.setenv("AI_CHAPTER_CHUNK_CHARS", "400")
    AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler_narrow))).convert(chapters)

    assert narrow["count"] > wide["count"]


def test_skipped_chunks_surface_as_conversion_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """被跳过的片段必须出现在结果里：否则用户拿到很薄的剧本却不知道原因。"""
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        title = prompt.split("本章标题：")[1].split("\n")[0]
        if "第一章" in title:
            return httpx.Response(504)      # 首章始终网关超时
        return chapter_response([scene_dict(source_chapter=title)])

    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    screenplay = converter.convert(NOVEL_CHAPTERS)

    assert screenplay.scenes                      # 其余片段仍出稿
    assert converter.last_run_warnings
    warning = converter.last_run_warnings[0]
    assert "失败已跳过" in warning
    assert "不完整" in warning


def test_warnings_reset_between_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    handler, _ = _progress_handler()
    converter = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    converter.last_run_warnings = ["上一轮的陈旧告警"]
    converter.convert(NOVEL_CHAPTERS)

    assert converter.last_run_warnings == []
