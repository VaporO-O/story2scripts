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
    assert screenplay.characters[0].arc
    assert isinstance(dialogue, Dialogue)
    assert dialogue.character == "character-1"
    assert dialogue.text == "出发吧。"


def test_demo_converter_uses_chapter_titles_as_source_titles() -> None:
    chapters = parse_chapters("第1章 A\n内容一\n第2章 B\n内容二\n第3章 C\n内容三")

    screenplay = DemoConverter().convert(chapters)

    assert screenplay.source.chapter_titles == ["第1章 A", "第2章 B", "第3章 C"]
    assert screenplay.scenes[1].source_chapter == "第2章 B"


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
    assert any("地点变化" in scene.beat for scene in first_chapter_scenes)
    assert any("情节转折" in scene.beat for scene in first_chapter_scenes)


def test_demo_converter_applies_adaptation_type_profile() -> None:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )

    screenplay = DemoConverter().convert(chapters, adaptation_type="广播剧")

    assert screenplay.adaptation_type == "广播剧"
    assert "声音提示" in screenplay.scenes[0].elements[0].text
    assert "广播剧冲突" in screenplay.scenes[0].conflict
    assert screenplay.scenes[0].beat.startswith("声音节拍")
    assert "声音设计" in screenplay.scenes[0].camera_hints[0]

