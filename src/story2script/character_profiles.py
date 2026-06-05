import re
from collections import Counter, defaultdict

from .parser import Chapter


PERSONALITY_KEYWORDS = [
    "敏感",
    "固执",
    "观察力强",
    "冷静",
    "勇敢",
    "谨慎",
    "冲动",
    "温柔",
    "孤僻",
    "理性",
]
RELATION_TERMS = ["姐姐", "弟弟", "哥哥", "妹妹", "父亲", "母亲", "老师", "同学"]
NON_NAME_KEYWORDS = [
    "失踪",
    "真相",
    "线索",
    "答案",
    "纸条",
    "时刻",
    "目标",
    "秘密",
    "内容",
]

RELATION_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{2,4})是(?P<other>[\u4e00-\u9fff]{2,4})的(?P<relation>[\u4e00-\u9fff]{1,4})"
)
SPEAKER_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,4})(?:说|问|喊|答道|低声道)[：:，,]?[“\"]")
NAME_WITH_RELATION_PATTERN = re.compile(
    rf"(?:{'|'.join(RELATION_TERMS)})([\u4e00-\u9fff]{{2,4}})"
    r"(?=说|问|喊|答道|低声道|[，。！？、\s]|$)"
)
CHANGE_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{2,4}).{0,16}从(?P<start>[^，。！？\n]{1,12})到(?P<end>[^，。！？\n]{1,12})"
)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]


def _looks_like_person_name(name: str) -> bool:
    return (
        2 <= len(name) <= 4
        and name not in RELATION_TERMS
        and not any(keyword in name for keyword in NON_NAME_KEYWORDS)
    )


def _candidate_name_counts(chapters: list[Chapter]) -> Counter[str]:
    counts: Counter[str] = Counter()

    def add_name(name: str) -> None:
        if _looks_like_person_name(name):
            counts[name] += 1

    for chapter in chapters:
        for name in SPEAKER_PATTERN.findall(chapter.content):
            add_name(name)
        for name in NAME_WITH_RELATION_PATTERN.findall(chapter.content):
            add_name(name)
        for match in RELATION_PATTERN.finditer(chapter.content):
            add_name(match.group("name"))
            add_name(match.group("other"))

    return counts


def _goal_for(name: str, text: str) -> str:
    triggers = ["想要", "希望", "决定", "发誓", "试图", "目标是"]
    action_triggers = ["寻找", "找到", "查明", "调查"]
    for sentence in _sentences(text):
        if name not in sentence:
            continue
        for trigger in triggers:
            if trigger in sentence:
                return sentence.split(trigger, 1)[1].strip("，,：: ")
        for trigger in action_triggers:
            if trigger in sentence:
                return f"{trigger}{sentence.split(trigger, 1)[1]}".strip("，,：: ")
    return "待作者进一步补充。"


def _personalities_for(name: str, text: str) -> list[str]:
    traits: list[str] = []
    for sentence in _sentences(text):
        if name not in sentence:
            continue
        for keyword in PERSONALITY_KEYWORDS:
            if keyword in sentence and keyword not in traits:
                traits.append(keyword)
    return traits or ["待作者进一步补充"]


def _key_change_for(name: str, text: str) -> str:
    for match in CHANGE_PATTERN.finditer(text):
        if match.group("name") == name:
            return f"从{match.group('start')}到{match.group('end')}"
    return "待作者进一步补充。"


def extract_character_profiles(chapters: list[Chapter]) -> list[dict]:
    """Extract lightweight character profile tables from parsed novel chapters."""
    name_counts = _candidate_name_counts(chapters)
    names = sorted(name_counts, key=lambda name: (-name_counts[name], name))
    full_text = "\n".join(chapter.content for chapter in chapters)
    relationships: dict[str, list[str]] = defaultdict(list)

    for match in RELATION_PATTERN.finditer(full_text):
        name = match.group("name")
        other = match.group("other")
        relation = match.group("relation")
        if not (_looks_like_person_name(name) and _looks_like_person_name(other)):
            continue
        relationships[name].append(f"是{other}的{relation}")

    appearances: dict[str, list[str]] = {}
    for name in names:
        appearances[name] = [chapter.title for chapter in chapters if name in chapter.content]

    ordered_names = sorted(
        names,
        key=lambda item: (-len(appearances[item]), -name_counts[item], item),
    )
    profiles: list[dict] = []
    for index, name in enumerate(ordered_names):
        profiles.append(
            {
                "name": name,
                "role": "主角" if index == 0 else "配角",
                "personality": "、".join(_personalities_for(name, full_text)),
                "goal": _goal_for(name, full_text),
                "relationships": relationships.get(name, ["待作者进一步补充。"]),
                "appearance_chapters": appearances[name],
                "key_change": _key_change_for(name, full_text),
            }
        )

    return profiles
