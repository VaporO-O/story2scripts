from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.screenplay import Dialogue, Screenplay


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

