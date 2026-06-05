from story2script.character_profiles import extract_character_profiles
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

    assert lin_che["name"] == "林澈"
    assert lin_che["role"] == "主角"
    assert "敏感" in lin_che["personality"]
    assert "固执" in lin_che["personality"]
    assert "观察力强" in lin_che["personality"]
    assert "姐姐失踪真相" in lin_che["goal"]
    assert lin_che["relationships"] == ["是林晚的弟弟"]
    assert lin_che["appearance_chapters"] == ["第1章 失踪", "第2章 线索", "第3章 追问"]
    assert lin_che["key_change"] == "从逃避到主动调查"

