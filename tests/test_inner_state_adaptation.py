from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.screenplay import Dialogue


def test_demo_converter_externalizes_inner_state() -> None:
    chapters = parse_chapters(
        "第一章 走廊\n"
        "林澈突然觉得背后一阵发冷，他隐约意识到，姐姐的失踪可能不是意外。\n"
        "第二章 纸条\n林澈继续调查。\n"
        "第三章 真相\n林澈停在门口。"
    )

    screenplay = DemoConverter().convert(chapters)
    scene = screenplay.scenes[0]
    dialogue = scene.elements[-1]

    assert any("停下脚步" in element.text for element in scene.elements if element.type == "action")
    assert isinstance(dialogue, Dialogue)
    assert dialogue.text == "不对……这不是意外。"
    assert dialogue.emotion == "紧张"
    assert scene.camera_hints == ["近景：林澈绷紧的表情。"]


def test_screenplay_schema_contains_camera_hints_and_dialogue_emotion() -> None:
    schema = DemoConverter().convert(
        parse_chapters("第一章 A\n林澈说：“走。”\n第二章 B\n内容\n第三章 C\n内容")
    ).model_dump(mode="json")

    assert "camera_hints" in schema["scenes"][0]
    assert "emotion" in schema["scenes"][0]["elements"][-1]
