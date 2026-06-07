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


def test_example_character_profiles_detect_real_names_not_modifiers() -> None:
    chapters = parse_chapters(load_example_novel()["novel_text"])

    names = {profile["name"] for profile in extract_character_profiles(chapters)}

    # 示例小说《低智商犯罪》中的真实人物应被识别出来。
    for expected in {"张一昂", "方超", "刘直", "李茜", "王瑞军", "高栋", "周卫东", "周荣"}:
        assert expected in names

    # 动词、副词、动作短语不应再被误判成人名（修复的核心问题）。
    for noise in {"直接", "点头", "马上", "皱眉", "继续", "幽幽地", "淡淡地", "好奇地",
                  "时间", "任务", "能力", "关系"}:
        assert noise not in names


def test_character_names_must_start_with_surname() -> None:
    chapters = parse_chapters(
        "第一章 蹲点\n他直接说：“动手。”她点头道：“好。”马上说：“快。”\n"
        "第二章 出手\n方超皱眉问：“稳住。”方超持枪站在窗边。方超低声下令。\n"
        "第三章 收尾\n方超淡淡地说：“收工。”"
    )

    names = {profile["name"] for profile in extract_character_profiles(chapters)}

    assert "方超" in names
    assert names == {"方超"}


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

