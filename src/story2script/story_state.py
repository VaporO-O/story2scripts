import re
from collections import defaultdict

from .character_profiles import extract_character_profiles
from .parser import Chapter
from .screenplay import GlobalCharacterState
from .screenplay import GlobalLocationState
from .screenplay import GlobalStoryState
from .screenplay import TimelineEvent


TIME_MARKER_PATTERN = re.compile(
    r"(清晨|早晨|上午|中午|下午|傍晚|黄昏|深夜|凌晨|夜里|第二天|"
    r"数日后|几天后|多年后|与此同时|morning|night|later|meanwhile)",
    re.IGNORECASE,
)
LOCATION_CUE_PATTERN = re.compile(
    r"(?:在|到|来到|进入|走进|回到|抵达|离开|穿过|转入|通往)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·]{2,12})"
)
LOCATION_SUFFIX_PATTERN = re.compile(
    r"^(.{0,10}?(?:楼|室|房|厅|馆|店|院|街|路|桥|港|站|城|村|镇|门口|"
    r"走廊|码头|广场|仓库|学校|医院|公寓|办公室|屋|山|湖|海边))"
)
LOCATION_ACTION_BOUNDARY_PATTERN = re.compile(
    r"(?:等待|停下|发现|看见|遇见|寻找|找到|调查|争执|质问|追问|站着|坐着|"
    r"走来|出现|说|问|喊|答道|低声道)"
)
NON_LOCATION_PATTERN = re.compile(
    r"(?:年前|年后|之前|之后|时刻|时候|当年|那天|今天|昨天|明天|"
    r"失踪|死亡|意外|事故|真相|秘密|回忆|记忆|事情|样子|声音|心里|内心)"
)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？.!?])\s*", text) if item.strip()]


def _first_sentence(text: str, limit: int = 90) -> str:
    sentences = _sentences(text)
    if not sentences:
        return "本章事件继续推进。"
    return sentences[0][:limit]


def _traits_from_text(text: str) -> list[str]:
    if not text or text == "待作者进一步补充":
        return []
    return [item for item in text.split("、") if item and item != "待作者进一步补充"]


def _clean_location_name(raw_name: str) -> str:
    name = raw_name.strip("，。！？、：:；;（）() ")
    if "的" in name:
        name = name.split("的", 1)[0]
    suffix_match = LOCATION_SUFFIX_PATTERN.match(name)
    if suffix_match:
        name = suffix_match.group(1)
    boundary_match = LOCATION_ACTION_BOUNDARY_PATTERN.search(name)
    if boundary_match and boundary_match.start() >= 2:
        name = name[: boundary_match.start()]
    name = name[:12]
    # 时间、事件或抽象描述被地点提示词误捕获时（如“在十年前父亲失踪的时刻”），予以剔除。
    if NON_LOCATION_PATTERN.search(name):
        return ""
    return name


def _extract_global_characters(chapters: list[Chapter]) -> list[GlobalCharacterState]:
    profiles = extract_character_profiles(chapters)
    characters: list[GlobalCharacterState] = []
    for index, profile in enumerate(profiles, start=1):
        appearance_chapters = profile["appearance_chapters"]
        characters.append(
            GlobalCharacterState(
                id=f"character-{index}",
                name=profile["name"],
                aliases=[],
                first_appearance=appearance_chapters[0] if appearance_chapters else chapters[0].title,
                appearance_chapters=appearance_chapters or [chapters[0].title],
                traits=_traits_from_text(profile["personality"]),
                goal=profile["goal"],
                arc=profile["key_change"],
                consistency_note=(
                    f"{profile['name']}在{len(appearance_chapters)}个章节出现；"
                    "后续分块转换必须保持姓名、性格、目标和人物弧光一致。"
                ),
            )
        )
    return characters


def _extract_locations(chapters: list[Chapter]) -> list[GlobalLocationState]:
    appearances: dict[str, list[str]] = defaultdict(list)
    descriptions: dict[str, str] = {}

    for chapter in chapters:
        for match in LOCATION_CUE_PATTERN.finditer(chapter.content):
            name = _clean_location_name(match.group("name"))
            if len(name) < 2:
                continue
            if chapter.title not in appearances[name]:
                appearances[name].append(chapter.title)
            descriptions.setdefault(name, _first_sentence(chapter.content))

    ordered_names = sorted(appearances, key=lambda item: (appearances[item][0], item))
    return [
        GlobalLocationState(
            id=f"location-{index}",
            name=name,
            first_appearance=appearances[name][0],
            appearance_chapters=appearances[name],
            description=descriptions[name],
        )
        for index, name in enumerate(ordered_names, start=1)
    ]


def _extract_timeline(chapters: list[Chapter]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for index, chapter in enumerate(chapters, start=1):
        marker_match = TIME_MARKER_PATTERN.search(chapter.content)
        events.append(
            TimelineEvent(
                id=f"event-{index}",
                order=index,
                chapter=chapter.title,
                time_marker=marker_match.group(1) if marker_match else "",
                summary=_first_sentence(chapter.content),
            )
        )
    return events


def extract_global_story_state(chapters: list[Chapter]) -> GlobalStoryState:
    """Build a fixed cross-chapter state table for conversion consistency."""
    return GlobalStoryState(
        characters=_extract_global_characters(chapters),
        locations=_extract_locations(chapters),
        timeline=_extract_timeline(chapters),
    )
