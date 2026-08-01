from story2script.converter import DemoConverter, _dialogue_from_text, _first_action_sentence
from story2script.examples import load_example_novel
from story2script.parser import parse_chapters
from story2script.screenplay import Dialogue, Screenplay


def test_dialogue_from_text_detects_post_quote_speaker() -> None:
    text = "“出发吧。”方超握紧手枪，站在窗边。"
    assert _dialogue_from_text(text, {"方超"}) == ("方超", "出发吧。")


def test_dialogue_from_text_ignores_unknown_post_quote_token() -> None:
    # 引号后跟的不是已知人物时，不应被当成说话人。
    assert _dialogue_from_text("“旧钟楼”三个字仍清晰可见。", {"林夏"}) is None


def test_first_action_sentence_skips_leading_dialogue() -> None:
    text = "“你有没有感觉，到处都是骗局？”方超手持一把枪，站在窗边。"
    assert _first_action_sentence(text).startswith("方超手持一把枪")


def test_demo_converter_post_quote_dialogue_and_clean_location() -> None:
    screenplay = DemoConverter().convert(
        parse_chapters(load_example_novel()["novel_text"]), title="低智商犯罪"
    )
    character_names = {character.name for character in screenplay.global_state.characters}
    id_to_name = {character.id: character.name for character in screenplay.global_state.characters}

    # 地点不应再是人物名（修复 heading 出现“刘直”的问题）。
    for scene in screenplay.scenes:
        assert scene.location not in character_names

    # 开场方超后置归属的台词应被识别为对白，而不是动作行。
    scene_one = screenplay.scenes[0]
    dialogues = [element for element in scene_one.elements if element.type == "dialogue"]
    assert dialogues
    assert id_to_name[dialogues[0].character] == "方超"
    assert all(
        not element.text.lstrip().startswith(("“", '"'))
        for element in scene_one.elements
        if element.type == "action"
    )


def test_demo_converter_returns_valid_screenplay() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")

    assert isinstance(screenplay, Screenplay)
    assert screenplay.title == "测试故事"
    assert screenplay.adaptation_type == "影视剧"
    assert screenplay.source.chapter_count == 3
    assert len(screenplay.global_state.timeline) == 3
    assert len(screenplay.scenes) == 3


def test_demo_converter_extracts_dialogue_character() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)
    dialogue = screenplay.scenes[0].elements[1]

    assert screenplay.characters[0].name == "林夏"
    assert screenplay.global_state.characters[0].id == screenplay.characters[0].id
    assert screenplay.characters[0].arc
    assert isinstance(dialogue, Dialogue)
    assert dialogue.character == "character-1"
    assert dialogue.text == "出发吧。"


def test_demo_converter_ignores_quotes_without_speaker() -> None:
    chapters = parse_chapters(
        "第一章 开始\n信纸上只有“旧钟楼”三个字仍清晰可见。林夏说：“这不可能是巧合。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)

    # 无说话人的引文（信纸上的文字）不应制造“叙述者”角色
    assert all(character.name != "叙述者" for character in screenplay.characters)
    assert all(
        character.name != "叙述者" for character in screenplay.global_state.characters
    )

    # 真正带说话人的台词仍被正确识别，即使它排在无主引文之后
    first_scene = screenplay.scenes[0]
    dialogues = [element for element in first_scene.elements if isinstance(element, Dialogue)]
    assert len(dialogues) == 1
    assert dialogues[0].character == screenplay.characters[0].id
    assert dialogues[0].text == "这不可能是巧合。"
    # 信纸上的“旧钟楼”不应被当作任何人的台词
    assert all(dialogue.text != "旧钟楼" for dialogue in dialogues)


def test_demo_converter_uses_chapter_titles_as_source_titles() -> None:
    chapters = parse_chapters("第1章 A\n内容一\n第2章 B\n内容二\n第3章 C\n内容三")

    screenplay = DemoConverter().convert(chapters)

    assert screenplay.source.chapter_titles == ["第1章 A", "第2章 B", "第3章 C"]
    assert screenplay.scenes[1].source_chapter == "第2章 B"


def test_demo_converter_populates_industry_scene_fields() -> None:
    chapters = parse_chapters(
        "第一章 开始\n深夜，林夏在码头捡到一封没有署名的信。林夏说：“先带回去。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)
    scene = screenplay.scenes[0]

    assert scene.int_ext == "EXT."
    assert scene.time_of_day == "NIGHT"
    assert scene.location == "码头"
    assert scene.heading == "EXT. 码头 - NIGHT"
    assert scene.characters_present == ["character-1"]
    assert scene.props == ["信"]
    assert {decision.target for decision in scene.dramatization_decisions} >= {
        "action",
        "dialogue",
        "scene_description",
    }


def test_demo_converter_drops_garbage_props() -> None:
    chapters = parse_chapters(
        "第一章 开始\n"
        "凌晨五点，林夏在码头捡到一封没有署名的信。"
        "信纸被海水浸湿，只有“旧钟楼”三个字仍清晰可见。\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)
    first_chapter_props = [
        prop
        for scene in screenplay.scenes
        if scene.source_chapter == "第一章 开始"
        for prop in scene.props
    ]

    # 真实道具仍被保留
    assert "信" in first_chapter_props
    # “只有……三个字仍清晰可见”不再被误抽成道具
    assert all("仍清晰" not in prop and "三个字" not in prop for prop in first_chapter_props)
    assert all("”" not in prop and "“" not in prop for prop in first_chapter_props)


def test_demo_converter_strips_chapter_prefix_from_fallback_location() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 旧钟楼\n雨水敲打着旧钟楼的玻璃。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)
    chapter_two_scene = next(
        scene for scene in screenplay.scenes if scene.source_chapter == "第二章 旧钟楼"
    )

    # 退回章节标题作地点时，不应带上“第二章”这种章节号前缀
    assert not chapter_two_scene.location.startswith("第二章")
    assert chapter_two_scene.location == "旧钟楼"
    assert chapter_two_scene.location in chapter_two_scene.heading


def test_demo_converter_splits_chapter_into_dramatic_scenes() -> None:
    chapters = parse_chapters(
        "第一章 开始\n"
        "清晨，林夏在码头等待。她走进旧钟楼。"
        "突然，守塔人推门而入。林夏说：“你为什么拦我？”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters)
    first_chapter_scenes = [
        scene for scene in screenplay.scenes if scene.source_chapter == "第一章 开始"
    ]

    assert len(first_chapter_scenes) > 1
    assert all(scene.goal for scene in first_chapter_scenes)
    assert all(scene.conflict for scene in first_chapter_scenes)
    assert all(scene.beat for scene in first_chapter_scenes)
    assert all(scene.subtext for scene in first_chapter_scenes)
    assert all(scene.dramatization_decisions for scene in first_chapter_scenes)
    assert any("地点变化" in scene.beat for scene in first_chapter_scenes)
    assert any("情节转折" in scene.beat for scene in first_chapter_scenes)


def test_demo_converter_uses_global_state_for_repeated_character() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“我会查下去。”\n"
        "第二章 转折\n林夏来到旧钟楼。\n"
        "第三章 结局\n林夏回到码头。林夏从逃避到主动调查。"
    )

    screenplay = DemoConverter().convert(chapters)
    lin_xia = screenplay.global_state.characters[0]

    assert lin_xia.name == "林夏"
    assert lin_xia.id == "character-1"
    assert lin_xia.appearance_chapters == ["第一章 开始", "第二章 转折", "第三章 结局"]
    assert screenplay.characters[0].id == lin_xia.id


def test_demo_converter_applies_adaptation_type_profile() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters, adaptation_type="广播剧")
    scene = screenplay.scenes[0]

    assert screenplay.adaptation_type == "广播剧"
    # 改编风格只体现在制作指导（camera_hints），不污染剧本正文
    assert any("声音设计" in hint for hint in scene.camera_hints)
    # 正文字段保持干净，不再混入写作指令式前缀
    assert "声音提示" not in scene.elements[0].text
    assert "广播剧冲突" not in scene.conflict
    assert not scene.beat.startswith("声音节拍")
    assert all("节拍" not in scene.beat for scene in screenplay.scenes)
    assert all(
        "调度" not in element.text and "提示" not in element.text
        for s in screenplay.scenes
        for element in s.elements
        if element.type == "action"
    )



def test_demo_converter_reports_progress() -> None:
    chapters = parse_chapters(load_example_novel()["novel_text"])
    events: list[tuple[int, int, str]] = []

    screenplay = DemoConverter().convert(
        chapters, title="低智商犯罪", progress_cb=lambda done, total, note: events.append((done, total, note))
    )

    assert screenplay.scenes
    assert events
    dones = [done for done, _, _ in events]
    assert dones == sorted(dones)              # 单调不减
    assert dones[-1] == events[-1][1]           # 终值到达总数
    assert any("拆分" in note for _, _, note in events)
    assert any("校验" in note for _, _, note in events)


def test_demo_converter_without_progress_cb_is_unaffected() -> None:
    chapters = parse_chapters(load_example_novel()["novel_text"])

    with_cb = DemoConverter().convert(chapters, title="x", progress_cb=lambda *args: None)
    without_cb = DemoConverter().convert(chapters, title="x")

    assert len(with_cb.scenes) == len(without_cb.scenes)
