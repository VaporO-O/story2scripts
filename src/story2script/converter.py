import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from .llm_client import LLMClient
from .parser import Chapter
from .screenplay import DEFAULT_ADAPTATION_TYPE
from .screenplay import Action
from .screenplay import AdaptationType
from .screenplay import Character
from .screenplay import Dialogue
from .screenplay import DramatizationDecision
from .screenplay import DramatizationTarget
from .screenplay import GlobalCharacterState
from .screenplay import GlobalStoryState
from .screenplay import IntExt
from .screenplay import Scene
from .screenplay import Screenplay
from .screenplay import SourceInfo
from .screenplay import TimeOfDay
from .story_state import extract_global_story_state


@dataclass(frozen=True)
class SceneSlice:
    text: str
    break_reasons: list[str]


@dataclass(frozen=True)
class AdaptationStyleProfile:
    production_hints: tuple[str, ...]
    arc_cue: str
    prompt_instruction: str


class Converter(Protocol):
    mode: str

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
    ) -> Screenplay:
        raise NotImplementedError


ADAPTATION_STYLE_PROFILES: dict[AdaptationType, AdaptationStyleProfile] = {
    "短剧": AdaptationStyleProfile(
        production_hints=("节奏提示：场景尽快抛出钩子和反转点。",),
        arc_cue="在密集冲突和反转中快速暴露人物选择。",
        prompt_instruction="短剧：节奏快，冲突密集，每场保留钩子和强反转。",
    ),
    "影视剧": AdaptationStyleProfile(
        production_hints=("镜头提示：建立空间关系后切入人物近景。",),
        arc_cue="在完整场景推进中逐步显露人物变化。",
        prompt_instruction="影视剧：场景完整，镜头感强，动作和人物反应清晰。",
    ),
    "舞台剧": AdaptationStyleProfile(
        production_hints=("舞台调度：标注人物站位、进退和视线方向。",),
        arc_cue="在舞台走位和对峙关系中显露人物转变。",
        prompt_instruction="舞台剧：增加舞台提示、人物走位、停顿和空间对峙。",
    ),
    "广播剧": AdaptationStyleProfile(
        production_hints=("声音设计：环境音先行，关键动作配合音效。",),
        arc_cue="在声音表演、旁白和沉默中显露人物变化。",
        prompt_instruction="广播剧：强调音效、旁白、声音距离和声音表演。",
    ),
    "分镜脚本": AdaptationStyleProfile(
        production_hints=("分镜：全景建立环境。", "分镜：近景捕捉人物反应。"),
        arc_cue="在镜头景别和画面调度变化中显露人物弧光。",
        prompt_instruction="分镜脚本：增加镜头、画面、景别、构图和镜头转换。",
    ),
}


SCENE_BREAK_PATTERNS = [
    (
        "时间变化",
        re.compile(
            r"(?:清晨|早晨|上午|中午|下午|傍晚|黄昏|深夜|凌晨|夜里|第二天|"
            r"数日后|几天后|多年后|与此同时|later|meanwhile|morning|night)",
            re.IGNORECASE,
        ),
    ),
    (
        "地点变化",
        re.compile(
            r"(?:来到|走进|进入|回到|抵达|到达|离开|穿过|推开|转入|"
            r"arrives?|enters?|leaves?|returns?)",
            re.IGNORECASE,
        ),
    ),
    (
        "人物进出",
        re.compile(
            r"(?:出现|走来|走进|冲进|推门而入|离开|退场|转身离去|"
            r"appears?|exits?|walks? in)",
            re.IGNORECASE,
        ),
    ),
    (
        "情节转折",
        re.compile(
            r"(?:突然|忽然|可是|然而|但|却|没想到|这时|就在这时|"
            r"suddenly|however|but|then)",
            re.IGNORECASE,
        ),
    ),
    (
        "冲突变化",
        re.compile(
            r"(?:质问|追问|争执|阻止|拒绝|威胁|拦住|抢过|打断|反驳|"
            r"confronts?|refuses?|blocks?|argues?)",
            re.IGNORECASE,
        ),
    ),
]
NIGHT_MARKER_PATTERN = re.compile(
    r"(?:夜里|深夜|凌晨|黄昏|傍晚|午夜|night|midnight|dusk)",
    re.IGNORECASE,
)
EXTERIOR_CUE_PATTERN = re.compile(
    r"(?:室外|屋外|门外|街|路|桥|港|码头|广场|公园|海边|山|湖|河|森林|巷|站)",
    re.IGNORECASE,
)
PROP_PHRASE_PATTERN = re.compile(
    r"(?:[一二两三四五六七八九十几数]|这|那|某)"
    r"(?:封|张|把|个|件|本|只|枚|串|部|台|盏|块|条|支|瓶|盒)"
    r"(?P<name>[一-鿿]{1,6})"
)
PROP_BOUNDARY_PATTERN = re.compile(
    r"(?:出现|落下|掉下|放在|拿起|递给|藏在|打开|关上|写着|留下|发现|看见|"
    r"说|问|喊|答道|低声道)"
)
PROP_NON_NOUN_PATTERN = re.compile(
    r"(?:仍|可见|清晰|样子|时候|事情|地方|声音|心里|内心|以为|觉得|意识)"
)
PROP_STOPWORDS = frozenset({"字", "事", "人", "话", "样", "声", "次", "下", "点"})
CHAPTER_PREFIX_PATTERN = re.compile(
    r"^\s*(?:第[零一二两三四五六七八九十百千万\d]+章|chapter\s+\d+)\s*",
    re.IGNORECASE,
)
PSYCHOLOGICAL_NARRATION_PATTERN = re.compile(
    r"(?:觉得|意识到|隐约意识到|心里|内心|害怕|担心|怀疑|明白|想起|希望|以为|"
    r"不安|发冷|恐惧|后悔|犹豫)"
)
ENVIRONMENT_NARRATION_PATTERN = re.compile(
    r"(?:雨|雪|雾|风|灯光|影子|空气|周围|窗|门|墙|走廊|房间|街|路|码头|"
    r"广场|夜色|天色|海|山|湖|河|声音)"
)
ACTION_NARRATION_PATTERN = re.compile(
    r"(?:走|跑|停|推|拉|拿|放|递|捡|打开|关上|回头|转身|离开|进入|来到|"
    r"发现|看见|望向|握住|坐下|站起|冲进)"
)


def _adaptation_style_profile(adaptation_type: AdaptationType) -> AdaptationStyleProfile:
    return ADAPTATION_STYLE_PROFILES[adaptation_type]


def _first_sentence(text: str, limit: int = 100) -> str:
    match = re.search(r"^(.+?[。！？.!?][”\"]?)", text.strip(), re.DOTALL)
    sentence = match.group(1).strip() if match else text.strip()
    return sentence[:limit] or "故事在沉默中展开。"


def _snippet(text: str, limit: int = 80) -> str:
    return _first_sentence(text, limit).strip()


def _text_units(text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"(?:\r?\n\s*){2,}", text) if item.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    return [
        match.group(0).strip()
        for match in re.finditer(r"[^。！？.!?]+[。！？.!?][”\"]?|[^。！？.!?]+$", text.strip())
        if match.group(0).strip()
    ]


def _scene_break_reasons(text: str) -> list[str]:
    return [label for label, pattern in SCENE_BREAK_PATTERNS if pattern.search(text)]


def _append_unique(items: list[str], values: list[str]) -> None:
    for value in values:
        if value not in items:
            items.append(value)


def _split_scene_slices(text: str) -> list[SceneSlice]:
    units = _text_units(text)
    if not units:
        return [SceneSlice(text="故事在沉默中展开。", break_reasons=["章节推进"])]

    slices: list[SceneSlice] = []
    current_units: list[str] = []
    current_reasons: list[str] = []

    for unit in units:
        reasons = _scene_break_reasons(unit)
        if current_units and reasons:
            slices.append(
                SceneSlice(
                    text="\n".join(current_units).strip(),
                    break_reasons=current_reasons or ["章节推进"],
                )
            )
            current_units = [unit]
            current_reasons = reasons
            continue

        current_units.append(unit)
        _append_unique(current_reasons, reasons)

    slices.append(
        SceneSlice(
            text="\n".join(current_units).strip(),
            break_reasons=current_reasons or ["章节推进"],
        )
    )
    return slices


def _dialogue_from_text(text: str) -> tuple[str, str] | None:
    for quote in re.finditer(r"[“\"]([^”\"]{2,100})[”\"]", text):
        prefix = text[max(0, quote.start() - 12) : quote.start()]
        speaker_match = re.search(
            r"([\u4e00-\u9fff]{2,5})(?:说|问|喊|答道|低声道)[，,:：]?$", prefix
        )
        if speaker_match:
            return speaker_match.group(1), quote.group(1)
    return None


def _inner_state_from_text(text: str) -> tuple[str, list[str], str, str, list[str]] | None:
    sentence_match = re.search(
        r"([\u4e00-\u9fff]{2,4})([^。！？!?]*(?:觉得|意识到|隐约意识到|发冷|不是意外)[^。！？!?]*)[。！？!?]",
        text,
    )
    if not sentence_match:
        return None

    character = sentence_match.group(1)
    for adverb in ["突然", "猛地", "一下"]:
        if character.endswith(adverb):
            character = character[: -len(adverb)]
    sentence = sentence_match.group(2)
    emotion = "紧张" if any(word in sentence for word in ["发冷", "不对", "不是意外"]) else "不安"
    line = "不对……这不是意外。" if "不是意外" in sentence else "等等，这里面不对。"
    actions = [
        f"{character}停下脚步，缓缓回头。",
        "周围的空气像是突然安静下来。",
    ]
    camera_hints = [f"近景：{character}绷紧的表情。"]
    return character, actions, line, emotion, camera_hints


def _scene_goal(
    text: str,
    dialogue: tuple[str, str] | None,
    inner_state: tuple[str, list[str], str, str, list[str]] | None,
) -> str:
    actor = dialogue[0] if dialogue else inner_state[0] if inner_state else "角色"
    return f"{actor}试图完成当前场景中的可见行动：{_first_sentence(text, 50)}"


def _scene_conflict(
    reasons: list[str],
    dialogue: tuple[str, str] | None,
    inner_state: tuple[str, list[str], str, str, list[str]] | None,
) -> str:
    if "冲突变化" in reasons:
        return "外部阻力升级，角色目标被迫重新调整。"
    if "情节转折" in reasons or inner_state:
        return "新的信息改变角色判断，形成场景冲突。"
    if dialogue:
        return "对白中的信息差让角色目标受到阻碍。"
    return "角色目标与未知信息之间形成潜在阻力。"


def _scene_subtext(
    dialogue: tuple[str, str] | None,
    inner_state: tuple[str, list[str], str, str, list[str]] | None,
) -> str:
    if inner_state:
        return "角色表面继续行动，内心判断已经发生变化。"
    if dialogue:
        return "对白之外，角色仍在试探对方的真实意图。"
    return "动作背后保留未明说的压力与选择。"


def _scene_beat(reasons: list[str]) -> str:
    return "、".join(reasons)


def _scene_time_of_day(text: str) -> TimeOfDay:
    return "NIGHT" if NIGHT_MARKER_PATTERN.search(text) else "DAY"


def _strip_chapter_prefix(title: str) -> str:
    stripped = CHAPTER_PREFIX_PATTERN.sub("", title).strip()
    return stripped or title


def _scene_location(text: str, chapter_title: str, global_state: GlobalStoryState) -> str:
    for location in global_state.locations:
        if location.name in text:
            return location.name
    return _strip_chapter_prefix(chapter_title)


def _scene_int_ext(text: str, location: str) -> IntExt:
    return "EXT." if EXTERIOR_CUE_PATTERN.search(f"{location}\n{text}") else "INT."


def _clean_prop_name(raw_name: str) -> str:
    name = raw_name.strip("，。！？、：:；;（）() ")
    if "的" in name:
        name = name.rsplit("的", 1)[-1]
    boundary_match = PROP_BOUNDARY_PATTERN.search(name)
    if boundary_match and boundary_match.start() >= 1:
        name = name[: boundary_match.start()]
    name = name.strip("，。！？、：:；;（）() ")[:6]
    # 剔除把动词、形容词或抽象词误当成道具的结果，只保留具体名词。
    if name in PROP_STOPWORDS or PROP_NON_NOUN_PATTERN.search(name):
        return ""
    return name


def _scene_props(text: str) -> list[str]:
    props: list[str] = []
    for match in PROP_PHRASE_PATTERN.finditer(text):
        name = _clean_prop_name(match.group("name"))
        if name and name not in props:
            props.append(name)
    return props


def _append_decision(
    decisions: list[DramatizationDecision],
    *,
    source_text: str,
    target: DramatizationTarget,
    rendering: str,
    reason: str,
) -> None:
    if any(decision.target == target and decision.source_text == source_text for decision in decisions):
        return
    decisions.append(
        DramatizationDecision(
            source_text=source_text,
            target=target,
            rendering=rendering,
            reason=reason,
        )
    )


def _dramatization_decisions(
    text: str,
    action_text: str,
    dialogue: tuple[str, str] | None,
    inner_state: tuple[str, list[str], str, str, list[str]] | None,
    scene_subtext: str,
) -> list[DramatizationDecision]:
    source = _snippet(text)
    decisions: list[DramatizationDecision] = []

    if ENVIRONMENT_NARRATION_PATTERN.search(text):
        _append_decision(
            decisions,
            source_text=source,
            target="scene_description",
            rendering=source,
            reason="环境、空间或氛围信息用于建立可拍摄的场景描述。",
        )

    if ACTION_NARRATION_PATTERN.search(text) or not dialogue:
        _append_decision(
            decisions,
            source_text=source,
            target="action",
            rendering=action_text,
            reason="可见行为或场面推进优先改写成动作行，而不是解释性旁白。",
        )

    if dialogue:
        _append_decision(
            decisions,
            source_text=f"“{dialogue[1]}”",
            target="dialogue",
            rendering=dialogue[1],
            reason="原文存在明确说话内容，可保留为推动冲突和信息交换的对白。",
        )

    if inner_state or PSYCHOLOGICAL_NARRATION_PATTERN.search(text):
        _append_decision(
            decisions,
            source_text=source,
            target="subtext",
            rendering=scene_subtext,
            reason="心理活动不直接搬成台词，而是通过潜台词、动作反应和镜头压力间接表现。",
        )

    if not decisions:
        _append_decision(
            decisions,
            source_text=source,
            target="action",
            rendering=action_text,
            reason="缺少明确对白和心理线索时，默认转成可见行动，保证剧本可演可拍。",
        )

    return decisions


def _append_character_id(scene_characters: list[str], character_id: str) -> None:
    if character_id and character_id not in scene_characters:
        scene_characters.append(character_id)


def _ensure_global_character(
    global_state: GlobalStoryState,
    name: str,
    chapter_title: str,
    arc_cue: str,
    consistency_note: str,
) -> None:
    for state in global_state.characters:
        if state.name != name:
            continue
        if chapter_title not in state.appearance_chapters:
            state.appearance_chapters.append(chapter_title)
        return

    character_id = f"character-{len(global_state.characters) + 1}"
    global_state.characters.append(
        GlobalCharacterState(
            id=character_id,
            name=name,
            aliases=[],
            first_appearance=chapter_title,
            appearance_chapters=[chapter_title],
            traits=[],
            goal="待作者进一步补充。",
            arc=arc_cue,
            consistency_note=consistency_note,
        )
    )


def _character_description_from_state(state: GlobalCharacterState) -> str:
    description = f"全局状态表识别角色；首次出场：{state.first_appearance}。"
    if state.traits:
        description = f"{description} 稳定特征：{'、'.join(state.traits)}。"
    return description


def _state_character_data(state: GlobalCharacterState) -> dict[str, str]:
    return {
        "id": state.id,
        "name": state.name,
        "description": _character_description_from_state(state),
        "motivation": state.goal,
        "arc": state.arc,
    }


def _source_info_from_chapters(chapters: list[Chapter]) -> SourceInfo:
    return SourceInfo(
        chapter_count=len(chapters),
        chapter_titles=[chapter.title for chapter in chapters],
    )


def _as_text(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _as_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_screenplay_character_data(characters: object) -> list[dict]:
    """Prune unknown keys and backfill required fields on AI character data."""
    allowed_fields = Character.model_fields.keys()
    normalized: list[dict] = []
    used_ids: set[str] = set()
    if not isinstance(characters, list):
        return normalized
    for index, character in enumerate(characters, start=1):
        if not isinstance(character, dict):
            continue
        pruned = {key: value for key, value in character.items() if key in allowed_fields}
        character_id = _as_text(pruned.get("id"))
        if not re.fullmatch(r"[a-z0-9_-]+", character_id) or character_id in used_ids:
            suffix = index
            character_id = f"character-{suffix}"
            while character_id in used_ids:
                suffix += 1
                character_id = f"character-{suffix}"
        used_ids.add(character_id)
        pruned["id"] = character_id
        pruned["name"] = _as_text(pruned.get("name")) or character_id
        pruned["description"] = _as_text(pruned.get("description"))
        pruned["motivation"] = _as_text(pruned.get("motivation"))
        pruned["arc"] = _as_text(pruned.get("arc")) or "待补充：人物弧光。"
        normalized.append(pruned)
    return normalized


def _resolve_character_ids(
    value: object, known_ids: set[str], name_to_id: dict[str, str]
) -> list[str]:
    resolved: list[str] = []
    for item in _as_text_list(value):
        if item in known_ids:
            candidate = item
        elif item in name_to_id:
            candidate = name_to_id[item]
        else:
            continue
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _normalize_int_ext(value: object, heading: str) -> IntExt:
    haystack = f"{_as_text(value)} {heading}".upper()
    if any(cue in haystack for cue in ("EXT", "外景", "室外", "户外", "门外")):
        return "EXT."
    return "INT."


def _normalize_time_of_day(value: object, heading: str) -> TimeOfDay:
    haystack = f"{_as_text(value)} {heading}".upper()
    night_markers = ("NIGHT", "MIDNIGHT", "DUSK", "夜", "晚", "黄昏", "傍晚", "凌晨", "深夜", "午夜")
    if any(marker in haystack for marker in night_markers):
        return "NIGHT"
    return "DAY"


def _location_from_heading(heading: str) -> str:
    match = re.match(
        r"^\s*(?:INT\.?|EXT\.?)\s*(?P<name>.+?)\s*-\s*(?:DAY|NIGHT)\s*$",
        heading,
        re.IGNORECASE,
    )
    return match.group("name").strip() if match else ""


def _normalize_heading(value: object, int_ext: str, location: str, time_of_day: str) -> str:
    heading = _as_text(value)
    if (
        heading.startswith(f"{int_ext} ")
        and location in heading
        and heading.endswith(f" - {time_of_day}")
    ):
        return heading
    return f"{int_ext} {location} - {time_of_day}"


def _normalize_scene_element(
    element: object, known_ids: set[str], name_to_id: dict[str, str]
) -> dict | None:
    if not isinstance(element, dict):
        return None
    element_type = _as_text(element.get("type")).lower()
    text = _as_text(element.get("text")) or _as_text(element.get("description"))
    if element_type == "dialogue":
        character = _as_text(element.get("character"))
        if character not in known_ids:
            character = name_to_id.get(character, "")
        if character in known_ids and text:
            return {
                "type": "dialogue",
                "character": character,
                "parenthetical": _as_text(element.get("parenthetical")),
                "text": text,
                "emotion": _as_text(element.get("emotion")),
            }
    if text:
        return {"type": "action", "text": text}
    return None


def _normalize_scene_elements(
    elements: object,
    known_ids: set[str],
    name_to_id: dict[str, str],
    fallback_text: str,
) -> list[dict]:
    normalized: list[dict] = []
    if isinstance(elements, list):
        for element in elements:
            resolved = _normalize_scene_element(element, known_ids, name_to_id)
            if resolved:
                normalized.append(resolved)
    if not normalized:
        normalized.append({"type": "action", "text": fallback_text})
    return normalized


def _normalize_dramatization_decisions(decisions: object, fallback_text: str) -> list[dict]:
    valid_targets = {"action", "dialogue", "subtext", "scene_description"}
    normalized: list[dict] = []
    if isinstance(decisions, list):
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            target = _as_text(decision.get("target")).lower()
            if target not in valid_targets:
                target = "action"
            source_text = _as_text(decision.get("source_text")) or fallback_text
            rendering = _as_text(decision.get("rendering")) or source_text
            reason = _as_text(decision.get("reason")) or "由 AI 输出补全的戏剧化决策。"
            normalized.append(
                {
                    "source_text": source_text,
                    "target": target,
                    "rendering": rendering,
                    "reason": reason,
                }
            )
    if not normalized:
        normalized.append(
            {
                "source_text": fallback_text,
                "target": "action",
                "rendering": fallback_text,
                "reason": "缺少分类决策时默认转为可见动作。",
            }
        )
    return normalized


def _normalize_screenplay_scene_data(
    scenes: object,
    known_ids: set[str],
    name_to_id: dict[str, str],
    chapter_titles: list[str],
) -> list[dict]:
    """Repair common AI scene deviations before strict schema validation."""
    allowed_fields = Scene.model_fields.keys()
    default_chapter = chapter_titles[0] if chapter_titles else ""
    normalized: list[dict] = []
    if not isinstance(scenes, list):
        return normalized
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        pruned = {key: value for key, value in scene.items() if key in allowed_fields}

        scene_id = _as_text(pruned.get("id"))
        if not re.fullmatch(r"scene-[0-9]+", scene_id):
            scene_id = f"scene-{index}"
        pruned["id"] = scene_id

        heading = _as_text(pruned.get("heading"))
        location = _as_text(pruned.get("location")) or _location_from_heading(heading) or "未指定地点"
        int_ext = _normalize_int_ext(pruned.get("int_ext"), heading)
        time_of_day = _normalize_time_of_day(pruned.get("time_of_day"), heading)
        pruned["location"] = location
        pruned["int_ext"] = int_ext
        pruned["time_of_day"] = time_of_day
        pruned["heading"] = _normalize_heading(heading, int_ext, location, time_of_day)

        source_chapter = _as_text(pruned.get("source_chapter"))
        if source_chapter not in chapter_titles:
            source_chapter = next(
                (title for title in chapter_titles if source_chapter and source_chapter in title),
                default_chapter,
            )
        pruned["source_chapter"] = source_chapter

        summary = _as_text(pruned.get("summary")) or f"第 {index} 场，待补充摘要。"
        pruned["summary"] = summary
        pruned["goal"] = _as_text(pruned.get("goal")) or "待补充：本场角色可见目标。"
        pruned["conflict"] = _as_text(pruned.get("conflict")) or "待补充：本场戏剧冲突。"
        pruned["beat"] = _as_text(pruned.get("beat")) or "待补充：本场节拍。"
        pruned["subtext"] = _as_text(pruned.get("subtext")) or "待补充：本场潜台词。"

        pruned["characters"] = _resolve_character_ids(pruned.get("characters"), known_ids, name_to_id)
        pruned["characters_present"] = _resolve_character_ids(
            pruned.get("characters_present"), known_ids, name_to_id
        )
        pruned["props"] = _as_text_list(pruned.get("props"))
        pruned["camera_hints"] = _as_text_list(pruned.get("camera_hints"))
        pruned["dramatization_decisions"] = _normalize_dramatization_decisions(
            pruned.get("dramatization_decisions"), summary
        )
        pruned["elements"] = _normalize_scene_elements(
            pruned.get("elements"), known_ids, name_to_id, summary
        )
        normalized.append(pruned)
    return normalized


def _format_validation_error(exc: ValidationError) -> str:
    first_error = exc.errors()[0] if exc.errors() else {"loc": (), "msg": str(exc)}
    location = ".".join(str(item) for item in first_error.get("loc", ())) or "root"
    return f"{location}: {first_error.get('msg', str(exc))}"


def _load_ai_screenplay_data(content: str) -> dict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 全文转换失败：模型返回内容不是有效 JSON。") from exc

    if not isinstance(data, dict):
        raise ValueError("AI 全文转换失败：模型返回的 JSON 必须是 Screenplay 对象。")
    return data


def _validate_ai_screenplay_data(data: dict) -> Screenplay:
    try:
        return Screenplay.model_validate(data)
    except ValidationError as exc:
        detail = _format_validation_error(exc)
        raise ValueError(f"AI 全文转换失败：模型返回结果不符合 Screenplay Schema（{detail}）。") from exc


class DemoConverter:
    """Deterministic converter used for offline demos and repeatable tests."""

    mode = "demo"

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
    ) -> Screenplay:
        style = _adaptation_style_profile(adaptation_type)
        global_state = extract_global_story_state(chapters)
        chapter_slices: list[tuple[Chapter, list[SceneSlice]]] = []

        for chapter in chapters:
            scene_slices = _split_scene_slices(chapter.content)
            chapter_slices.append((chapter, scene_slices))
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text)
                inner_state = _inner_state_from_text(scene_slice.text)
                if dialogue:
                    _ensure_global_character(
                        global_state=global_state,
                        name=dialogue[0],
                        chapter_title=chapter.title,
                        arc_cue=style.arc_cue,
                        consistency_note="转换时补充识别；后续场景应保持称呼和语气一致。",
                    )
                if inner_state:
                    _ensure_global_character(
                        global_state=global_state,
                        name=inner_state[0],
                        chapter_title=chapter.title,
                        arc_cue=style.arc_cue,
                        consistency_note="转换时补充识别；后续场景应保持称呼和人物判断一致。",
                    )

        characters = [
            Character(
                id=state.id,
                name=state.name,
                description=_character_description_from_state(state),
                motivation=state.goal,
                arc=state.arc if state.arc != "待作者进一步补充。" else style.arc_cue,
            )
            for state in global_state.characters
        ]
        character_ids = {character.name: character.id for character in characters}

        scenes: list[Scene] = []
        scene_index = 1
        for chapter, scene_slices in chapter_slices:
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text)
                inner_state = _inner_state_from_text(scene_slice.text)
                elements: list[Action | Dialogue] = [
                    Action(
                        type="action",
                        text=_first_sentence(scene_slice.text),
                    )
                ]
                scene_characters: list[str] = []
                camera_hints: list[str] = list(style.production_hints)

                for state in global_state.characters:
                    if state.name in scene_slice.text:
                        _append_character_id(scene_characters, state.id)

                if dialogue:
                    character_id = character_ids[dialogue[0]]
                    _append_character_id(scene_characters, character_id)
                    elements.append(
                        Dialogue(
                            type="dialogue",
                            character=character_id,
                            parenthetical="",
                            text=dialogue[1],
                        )
                    )

                if inner_state:
                    character_name, actions, _line, _emotion, hints = inner_state
                    character_id = character_ids[character_name]
                    _append_character_id(scene_characters, character_id)
                    elements.extend(Action(type="action", text=action) for action in actions)
                    camera_hints.extend(hints)

                location = _scene_location(scene_slice.text, chapter.title, global_state)
                int_ext = _scene_int_ext(scene_slice.text, location)
                time_of_day = _scene_time_of_day(scene_slice.text)
                action_text = elements[0].text
                scene_subtext = _scene_subtext(dialogue, inner_state)
                scenes.append(
                    Scene(
                        id=f"scene-{scene_index}",
                        heading=f"{int_ext} {location} - {time_of_day}",
                        int_ext=int_ext,
                        time_of_day=time_of_day,
                        location=location,
                        source_chapter=chapter.title,
                        summary=_first_sentence(scene_slice.text, 60),
                        goal=_scene_goal(scene_slice.text, dialogue, inner_state),
                        conflict=_scene_conflict(
                            scene_slice.break_reasons,
                            dialogue,
                            inner_state,
                        ),
                        beat=_scene_beat(scene_slice.break_reasons),
                        subtext=scene_subtext,
                        characters=scene_characters,
                        characters_present=list(scene_characters),
                        props=_scene_props(scene_slice.text),
                        dramatization_decisions=_dramatization_decisions(
                            scene_slice.text,
                            action_text,
                            dialogue,
                            inner_state,
                            scene_subtext,
                        ),
                        elements=elements,
                        camera_hints=camera_hints,
                    )
                )
                scene_index += 1

        resolved_title = title.strip() or "未命名改编"
        return Screenplay(
            schema_version="1.0",
            title=resolved_title,
            genre=genre.strip(),
            adaptation_type=adaptation_type,
            logline=f"以{adaptation_type}方式围绕《{resolved_title}》核心冲突展开的剧本初稿。",
            source=_source_info_from_chapters(chapters),
            global_state=global_state,
            characters=characters,
            scenes=scenes,
        )


class AIConverter:
    """OpenAI-compatible LLM converter.

    The provider is intentionally configured by environment variables so the
    project is not tied to one vendor.
    """

    mode = "ai"

    def __init__(self, llm_client: LLMClient | None = None, client=None) -> None:
        self.llm_client = llm_client or LLMClient(client=client, usage_label="AI mode")

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
    ) -> Screenplay:
        style = _adaptation_style_profile(adaptation_type)
        global_state = extract_global_story_state(chapters)
        source_text = "\n\n".join(f"{chapter.title}\n{chapter.content}" for chapter in chapters)
        prompt = (
            "你是专业影视编剧。请将小说改编成完整的 Story2Script Screenplay JSON 对象。"
            "重点：小说心理描写不能原样照搬，要外化为动作、对白 emotion 和 camera_hints。"
            "剧本要按时间变化、地点变化、人物进出、情节转折和冲突变化拆成多个场景。"
            "只返回可被 json.loads 解析的 JSON 对象，不要 YAML、Markdown 或解释文字。"
            "后端会执行 llm_json -> json.loads -> Screenplay.model_validate -> screenplay_to_yaml；"
            "任何缺字段或类型错误都会被拒绝。\n\n"
            f"标题：{title or '请根据内容拟定'}\n"
            f"类型：{genre or '请根据内容判断'}\n"
            f"改编类型：{adaptation_type}\n"
            f"改编要求：{style.prompt_instruction}\n"
            "全局状态表是固定上下文，分块转换时必须保持人物姓名、性格、地点和时间线一致："
            f"{json.dumps(global_state.model_dump(mode='json'), ensure_ascii=False)}\n"
            'Schema 要点：顶层 schema_version 必须固定为字符串 "1.0"，不要写成数字、v1.0 或其它值; '
            "title, genre, logline, characters, scenes 必须存在; "
            "source 会由后端根据章节解析结果回填为对象，不要输出字符串或数组; "
            f"顶层必须包含 adaptation_type，且 adaptation_type 必须等于 {adaptation_type}; "
            "顶层必须包含 global_state; 每个 character 必须包含 arc; "
            "顶层 characters 的每个对象只能包含 id, name, description, motivation, arc; "
            "aliases、first_appearance、appearance_chapters、traits、goal 和 consistency_note "
            "只能出现在 global_state.characters，不要放入顶层 characters; "
            "scene 必须包含 int_ext, time_of_day, location, characters_present, props, "
            "dramatization_decisions, goal, conflict, beat, subtext, elements 和 camera_hints; "
            "heading 必须与 int_ext/location/time_of_day 对齐，使用类似 INT. LIBRARY - DAY 的 slug line; "
            "每个 scene 的 dramatization_decisions 必须显式记录叙述到戏剧表达的分类决策，"
            "target 只能是 action、dialogue、subtext、scene_description。"
            "分类规则：可见行为转 action；明确说话内容或需要外化的信息交换转 dialogue；"
            "心理活动、情绪判断和未说出口的意图转 subtext，不能直接搬成台词；"
            "天气、空间、背景和氛围转 scene_description。"
            "dialogue 可包含 emotion。\n\n"
            f"小说原文：\n{source_text}"
        )
        content = self.llm_client.complete_json(prompt)
        screenplay_data = _load_ai_screenplay_data(content)
        screenplay_data["source"] = _source_info_from_chapters(chapters).model_dump(mode="json")
        screenplay_data["global_state"] = global_state.model_dump(mode="json")
        raw_characters = screenplay_data.get("characters")
        if not isinstance(raw_characters, list):
            raw_characters = []
        screenplay_data["characters"] = raw_characters
        existing_characters = {
            character.get("id"): character
            for character in raw_characters
            if isinstance(character, dict)
        }
        for state in global_state.characters:
            state_data = _state_character_data(state)
            if state.id in existing_characters:
                existing_characters[state.id].update(
                    {
                        "name": state.name,
                        "description": existing_characters[state.id].get("description")
                        or state_data["description"],
                        "motivation": existing_characters[state.id].get("motivation")
                        or state_data["motivation"],
                        "arc": existing_characters[state.id].get("arc") or state_data["arc"],
                    }
                )
            else:
                screenplay_data["characters"].append(state_data)
        screenplay_data["characters"] = _normalize_screenplay_character_data(
            screenplay_data["characters"]
        )

        resolved_title = _as_text(screenplay_data.get("title")) or title.strip() or "未命名改编"
        screenplay_data["schema_version"] = "1.0"
        screenplay_data["title"] = resolved_title
        screenplay_data["genre"] = _as_text(screenplay_data.get("genre")) or genre.strip()
        screenplay_data["logline"] = (
            _as_text(screenplay_data.get("logline"))
            or f"以{adaptation_type}方式围绕《{resolved_title}》核心冲突展开的剧本初稿。"
        )
        if not _as_text(screenplay_data.get("adaptation_type")):
            screenplay_data["adaptation_type"] = adaptation_type

        known_ids = {character["id"] for character in screenplay_data["characters"]}
        name_to_id = {
            character["name"]: character["id"]
            for character in screenplay_data["characters"]
            if character.get("name")
        }
        screenplay_data["scenes"] = _normalize_screenplay_scene_data(
            screenplay_data.get("scenes"),
            known_ids,
            name_to_id,
            [chapter.title for chapter in chapters],
        )
        if not screenplay_data["scenes"]:
            raise ValueError("AI 全文转换失败：模型没有返回任何有效场景。")

        screenplay = _validate_ai_screenplay_data(screenplay_data)
        if screenplay.adaptation_type != adaptation_type:
            raise ValueError(
                "AI 全文转换失败：模型返回的 adaptation_type 必须与请求一致"
                f"（expected={adaptation_type}, actual={screenplay.adaptation_type}）。"
            )
        return screenplay


def get_converter(mode: str = "demo") -> Converter:
    if mode == "demo":
        return DemoConverter()
    if mode == "ai":
        return AIConverter()
    raise ValueError(f"Unsupported converter mode: {mode}")

