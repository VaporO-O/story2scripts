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
    assert isinstance(dialogue, Dialogue)
    assert dialogue.character == "character-1"
    assert dialogue.text == "出发吧。"


def test_demo_converter_uses_chapter_titles_as_source_titles() -> None:
    chapters = parse_chapters("第1章 A\n内容一\n第2章 B\n内容二\n第3章 C\n内容三")

    screenplay = DemoConverter().convert(chapters)

    assert screenplay.source.chapter_titles == ["第1章 A", "第2章 B", "第3章 C"]
    assert screenplay.scenes[1].source_chapter == "第2章 B"

