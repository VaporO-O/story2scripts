from story2script.converter import DemoConverter
from story2script.parser import parse_chapters


def test_demo_converter_externalizes_inner_state() -> None:
    chapters = parse_chapters(
        "第一章 走廊\n"
        "林澈突然觉得背后一阵发冷，他隐约意识到，姐姐的失踪可能不是意外。\n"
        "第二章 纸条\n林澈继续调查。\n"
        "第三章 真相\n林澈停在门口。"
    )

    screenplay = DemoConverter().convert(chapters)
    scene = screenplay.scenes[0]
    targets = {decision.target for decision in scene.dramatization_decisions}

    assert any("停下脚步" in element.text for element in scene.elements if element.type == "action")
    assert not any(element.type == "dialogue" for element in scene.elements)
    assert "subtext" in targets
    assert any("心理活动不直接搬成台词" in decision.reason for decision in scene.dramatization_decisions)
    assert "近景：林澈绷紧的表情。" in scene.camera_hints


def test_screenplay_schema_contains_camera_hints_and_dramatization_decisions() -> None:
    schema = DemoConverter().convert(
        parse_chapters("第一章 A\n林澈说：“走。”\n第二章 B\n内容\n第三章 C\n内容")
    ).model_dump(mode="json")

    assert "camera_hints" in schema["scenes"][0]
    assert "emotion" in schema["scenes"][0]["elements"][-1]
    assert "dramatization_decisions" in schema["scenes"][0]
    assert schema["scenes"][0]["dramatization_decisions"][0]["target"] in {
        "action",
        "dialogue",
        "subtext",
        "scene_description",
    }
