from story2script.character_profiles import extract_character_profiles
from story2script.examples import load_example_novel
from story2script.parser import parse_chapters


def test_extract_character_profile_table_fields() -> None:
    chapters = parse_chapters(
        "第1章 失踪\n"
        "林澈是林晚的弟弟。林澈敏感、固执、观察力强。"
        "林澈想要找到姐姐失踪真相。林澈说：“我会查下去。”\n"
        "第2章 线索\n"
        "林澈继续调查，林晚留下的纸条出现了。\n"
        "第3章 追问\n"
        "林澈从逃避到主动调查。"
    )

    profiles = extract_character_profiles(chapters)
    lin_che = profiles[0]
    names = {profile["name"] for profile in profiles}

    assert lin_che["name"] == "林澈"
    assert lin_che["role"] == "主角"
    assert "失踪真相" not in names
    assert "敏感" in lin_che["personality"]
    assert "固执" in lin_che["personality"]
    assert "观察力强" in lin_che["personality"]
    assert "姐姐失踪真相" in lin_che["goal"]
    assert lin_che["relationships"] == ["是林晚的弟弟"]
    assert lin_che["appearance_chapters"] == ["第1章 失踪", "第2章 线索", "第3章 追问"]
    assert lin_che["key_change"] == "从逃避到主动调查"


def test_extract_character_profiles_uses_stable_name_tiebreak() -> None:
    chapters = parse_chapters(
        "第1章 开场\n"
        "王明说：“先去码头。”李安说：“我留下。”\n"
        "第2章 线索\n"
        "雨停了。\n"
        "第3章 收束\n"
        "灯亮了。"
    )

    profiles = extract_character_profiles(chapters)

    assert [profile["name"] for profile in profiles[:2]] == ["李安", "王明"]
    assert profiles[0]["role"] == "主角"


def test_example_character_profiles_ignore_relation_descriptions() -> None:
    chapters = parse_chapters(load_example_novel()["novel_text"])

    profiles = extract_character_profiles(chapters)

    assert not any("失踪" in profile["name"] for profile in profiles)


def test_relation_prefix_candidates_require_name_evidence() -> None:
    chapters = parse_chapters(
        "第1章 暗号\n"
        "周远说：“先查码头。”周远寻找母亲旧信暗号。老师张宁说：“别急。”\n"
        "第2章 追查\n"
        "答案是故事的核心。周远继续调查。\n"
        "第3章 回声\n"
        "张宁说：“看这里。”"
    )

    names = {profile["name"] for profile in extract_character_profiles(chapters)}

    assert "旧信暗号" not in names
    assert "故事" not in names
    assert "老师张宁" not in names
    assert "张宁" in names

