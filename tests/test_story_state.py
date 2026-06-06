from story2script.parser import parse_chapters
from story2script.story_state import extract_global_story_state


def test_extract_global_story_state_keeps_character_consistency_across_chapters() -> None:
    chapters = parse_chapters(
        "第一章 雾起\n"
        "清晨，林夏在码头等待。林夏说：“我会查下去。”林夏是林晚的妹妹。\n"
        "第二章 旧楼\n"
        "林夏来到旧钟楼。张宁说：“别进去。”\n"
        "第三章 潮汐\n"
        "夜里，林夏回到码头，林夏从逃避到主动调查。"
    )

    global_state = extract_global_story_state(chapters)
    lin_xia = next(character for character in global_state.characters if character.name == "林夏")

    assert lin_xia.id == "character-1"
    assert lin_xia.appearance_chapters == ["第一章 雾起", "第二章 旧楼", "第三章 潮汐"]
    assert lin_xia.arc == "从逃避到主动调查"
    assert "必须保持" in lin_xia.consistency_note


def test_extract_global_story_state_builds_locations_and_timeline() -> None:
    chapters = parse_chapters(
        "第一章 雾起\n"
        "清晨，林夏在码头等待。林夏说：“我会查下去。”\n"
        "第二章 旧楼\n"
        "下午，林夏来到旧钟楼。\n"
        "第三章 潮汐\n"
        "夜里，林夏回到码头。"
    )

    global_state = extract_global_story_state(chapters)
    location_names = {location.name for location in global_state.locations}

    assert "码头" in location_names
    assert "旧钟楼" in location_names
    assert [event.order for event in global_state.timeline] == [1, 2, 3]
    assert global_state.timeline[0].time_marker == "清晨"
    assert global_state.timeline[2].chapter == "第三章 潮汐"


def test_extract_locations_rejects_time_and_event_phrases() -> None:
    chapters = parse_chapters(
        "第一章 雾起\n"
        "林夏来到旧钟楼。墙上的钟停在十年前父亲失踪的时刻。\n"
        "第二章 旧楼\n林夏在码头等待。\n"
        "第三章 潮汐\n夜里，林夏回到码头。"
    )

    global_state = extract_global_story_state(chapters)
    location_names = {location.name for location in global_state.locations}

    # 真实地点保留
    assert "旧钟楼" in location_names
    assert "码头" in location_names
    # “十年前父亲失踪”这类时间/事件短语不应被当成地点
    assert all("失踪" not in name and "年前" not in name for name in location_names)
