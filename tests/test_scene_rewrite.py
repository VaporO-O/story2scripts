from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.scene_rewrite import rewrite_scene
from story2script.screenplay import Action, Dialogue


def sample_screenplay():
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )
    return DemoConverter().convert(chapters, title="测试故事", genre="剧情")


def test_rewrite_scene_updates_only_target_scene_dialogue() -> None:
    screenplay = sample_screenplay()
    untouched_scene = screenplay.scenes[1].model_dump(mode="json")

    updated, message = rewrite_scene(screenplay, "scene-1", "rewrite_dialogue")
    dialogue = updated.scenes[0].elements[1]

    assert message == "已重新生成本场对白。"
    assert isinstance(dialogue, Dialogue)
    assert "把话说清楚" in dialogue.text
    assert updated.scenes[1].model_dump(mode="json") == untouched_scene


def test_rewrite_scene_can_strengthen_conflict() -> None:
    screenplay = sample_screenplay()

    updated, _ = rewrite_scene(screenplay, "scene-1", "strengthen_conflict")

    assert "冲突升级" in updated.scenes[0].conflict
    assert "冲突升级" in updated.scenes[0].beat
    assert isinstance(updated.scenes[0].elements[0], Action)


def test_rewrite_scene_can_adjust_character_voice() -> None:
    screenplay = sample_screenplay()
    character_id = screenplay.characters[0].id

    updated, _ = rewrite_scene(
        screenplay,
        "scene-1",
        "adjust_character_voice",
        character_id=character_id,
        tone="更锋利",
    )
    dialogue = updated.scenes[0].elements[1]

    assert isinstance(dialogue, Dialogue)
    assert dialogue.character == character_id
    assert dialogue.parenthetical == "更锋利"
    assert dialogue.emotion == "更锋利"


def test_rewrite_scene_rejects_unknown_scene() -> None:
    screenplay = sample_screenplay()

    try:
        rewrite_scene(screenplay, "missing-scene", "add_camera_hints")
    except ValueError as exc:
        assert "missing-scene" in str(exc)
    else:
        raise AssertionError("Expected missing scene to raise ValueError")
