import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from .character_profiles_ai import PROFILE_PLACEHOLDERS, AICharacterProfiler
from .llm_client import LLMClient, is_fatal_error, loads_json_object
from .parser import Chapter
from .prompt_catalog import CONVERSION_CHUNK_PROMPT
from .rag import build_story_knowledge, rag_top_k
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
from .security import DATA_FENCE_NOTICE
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


AI_CHAPTER_CHUNK_CHAR_LIMIT = 1800


class Converter(Protocol):
    mode: str
    # 本次转换的非致命告警（如被跳过的失败片段）。调用方在 convert 返回后读取，
    # 用于把"为什么剧本这么薄"如实告诉用户。
    last_run_warnings: list[str]

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
        progress_cb=None,
        scene_cb=None,
        meta_cb=None,
    ) -> Screenplay:
        """progress_cb(done, total, note)：可选，与 Agent / 团队回调同款签名。

        scene_cb(scene_dict)：可选，场景定稿即回调一次，用于边生成边显示。
        它与 progress_cb 是两条独立通道——进度只关心"到哪了"，场景关心"生成了
        什么"，混在一条回调里会让二者的语义互相牵制。

        meta_cb(meta_dict)：可选，在第一个场景之前回调一次，带上标题与人物名册。
        流式场景里的说话人是 character id，没有名册就只能显示"character-1"。
        """
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


def _split_oversized_text(text: str, limit: int) -> list[str]:
    units = _text_units(text)
    if not units:
        stripped = text.strip()
        return [stripped] if stripped else []

    chunks: list[str] = []
    current_units: list[str] = []
    current_length = 0

    def flush_current() -> None:
        nonlocal current_length
        if current_units:
            chunks.append("\n".join(current_units).strip())
            current_units.clear()
            current_length = 0

    for unit in units:
        if len(unit) > limit:
            flush_current()
            chunks.extend(
                unit[index : index + limit].strip()
                for index in range(0, len(unit), limit)
                if unit[index : index + limit].strip()
            )
            continue

        separator_length = 1 if current_units else 0
        projected_length = current_length + separator_length + len(unit)
        if current_units and projected_length > limit:
            flush_current()
            projected_length = len(unit)

        current_units.append(unit)
        current_length = projected_length

    flush_current()
    return chunks


def _chapter_text_chunks(
    chapter: Chapter,
    limit: int = AI_CHAPTER_CHUNK_CHAR_LIMIT,
) -> list[str]:
    chunks: list[str] = []
    current_slices: list[str] = []
    current_length = 0

    def flush_current() -> None:
        nonlocal current_length
        if current_slices:
            chunks.append("\n\n".join(current_slices).strip())
            current_slices.clear()
            current_length = 0

    for scene_slice in _split_scene_slices(chapter.content):
        text = scene_slice.text.strip()
        if not text:
            continue

        if len(text) > limit:
            flush_current()
            chunks.extend(_split_oversized_text(text, limit))
            continue

        separator_length = 2 if current_slices else 0
        projected_length = current_length + separator_length + len(text)
        if current_slices and projected_length > limit:
            flush_current()
            projected_length = len(text)

        current_slices.append(text)
        current_length = projected_length

    flush_current()
    fallback = chapter.content.strip()
    return chunks or ([fallback] if fallback else [])


def _resolve_known_name(raw: str, known_names: set[str]) -> str | None:
    """把对白/心理描写正则抓到的原始说话人解析为已识别的人物姓名。

    说话人正则会连带抓到修饰语（如“吴主任寻思 / 幽幽地 / 继续”）。这里只把能映射到
    全局状态表中真实人物的片段当作说话人；映射不到的（多为副词、动作短语）一律丢弃，
    避免把垃圾词注册成新人物、扰乱台词归属。
    """
    if raw in known_names:
        return raw
    matches = [name for name in known_names if name in raw]
    if matches:
        return max(matches, key=len)
    return None


def _dialogue_from_text(text: str, known_names: set[str]) -> tuple[str, str] | None:
    for quote in re.finditer(r"[“\"]([^”\"]{2,100})[”\"]", text):
        prefix = text[max(0, quote.start() - 12) : quote.start()]
        speaker_match = re.search(
            r"([\u4e00-\u9fff]{2,5})(?:说|问|喊|答道|低声道)[，,:：]?$", prefix
        )
        if speaker_match:
            speaker = _resolve_known_name(speaker_match.group(1), known_names)
            if speaker:
                return speaker, quote.group(1)
        # 说话人后置：原文常写成 “……”方超手持枪 / “……”，方超说。引号后紧跟的已知
        # 人物名同样视为说话人，避免这类对白被当成动作行。
        suffix = text[quote.end() : quote.end() + 8]
        post_match = re.match(r"^[，,、：:\s]*([一-鿿]{2,5})", suffix)
        if post_match:
            speaker = _resolve_known_name(post_match.group(1), known_names)
            if speaker:
                return speaker, quote.group(1)
    return None


def _first_action_sentence(text: str, limit: int = 100) -> str:
    """返回首个非对白叙述句，避免把开场对白（含引号内的句末标点）塞进动作行。"""
    narration = re.sub(r"[“\"][^”\"]*[”\"]", "", text)
    for sentence in re.split(r"(?<=[。！？!?])", narration):
        cleaned = sentence.strip(" 　，,、—-…")
        if len(cleaned) >= 2:
            return cleaned[:limit]
    return ""


def _inner_state_from_text(
    text: str, known_names: set[str]
) -> tuple[str, list[str], str, str, list[str]] | None:
    sentence_match = re.search(
        r"([\u4e00-\u9fff]{2,4})([^。！？!?]*(?:觉得|意识到|隐约意识到|发冷|不是意外)[^。！？!?]*)[。！？!?]",
        text,
    )
    if not sentence_match:
        return None

    character = _resolve_known_name(sentence_match.group(1), known_names)
    if not character:
        return None
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
    narration: str,
    dialogue: tuple[str, str] | None,
    inner_state: tuple[str, list[str], str, str, list[str]] | None,
) -> str:
    actor = dialogue[0] if dialogue else inner_state[0] if inner_state else "角色"
    return f"{actor}试图完成当前场景中的可见行动：{_first_sentence(narration, 50)}"


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
    # 用非对白叙述句作为场景描述/动作类决策的原文来源，避免把对白引号当成叙述。
    source = action_text or _snippet(text)
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


_DIALOGUE_TYPE_ALIASES = {
    "dialogue",
    "dialog",
    "line",
    "speech",
    "utterance",
    "台词",
    "对白",
    "对话",
}
_ACTION_TYPE_ALIASES = {
    "action",
    "actions",
    "description",
    "scene_description",
    "stage_direction",
    "narration",
    "动作",
    "动作行",
    "场景描述",
    "旁白",
}
_ELEMENT_TEXT_KEYS = (
    "text",
    "line",
    "dialogue",
    "speech",
    "utterance",
    "content",
    "description",
    "台词",
    "对白",
    "内容",
)
_ELEMENT_CHARACTER_KEYS = (
    "character",
    "speaker",
    "name",
    "role",
    "actor",
    "人物",
    "角色",
    "说话人",
)


def _collect_raw_elements(scene: dict) -> list:
    """Gather scene elements, merging alternative keys the LLM may use.

    Some models emit dialogue / action under their own top-level keys instead of
    a single ``elements`` list. They are merged here so the content is preserved
    before the Scene's forbidden extra fields get pruned away.
    """
    collected: list = []
    elements = scene.get("elements")
    if isinstance(elements, list):
        collected.extend(elements)
    for key, element_type in (
        ("dialogue", "dialogue"),
        ("dialogues", "dialogue"),
        ("action", "action"),
        ("actions", "action"),
    ):
        value = scene.get(key)
        items = value if isinstance(value, list) else [value] if value else []
        for item in items:
            if isinstance(item, dict):
                collected.append({**item, "type": item.get("type") or element_type})
            elif isinstance(item, str) and item.strip():
                collected.append({"type": element_type, "text": item.strip()})
    return collected


def _first_text_by_keys(element: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = element.get(key)
        if isinstance(value, (dict, list)):
            continue
        text = _as_text(value)
        if text:
            return text
    return ""


def _normalize_element_type(value: object) -> str:
    element_type = _as_text(value).casefold()
    if element_type in _DIALOGUE_TYPE_ALIASES:
        return "dialogue"
    if element_type in _ACTION_TYPE_ALIASES:
        return "action"
    return element_type


def _resolve_character_reference(
    value: object,
    known_ids: set[str],
    name_to_id: dict[str, str],
) -> str:
    raw = _as_text(value)
    if not raw:
        return ""

    candidates = [
        raw,
        re.sub(r"[（(][^）)]{1,20}[）)]", "", raw).strip(" \t-—:："),
    ]
    for candidate in candidates:
        if candidate in known_ids:
            return candidate
        if candidate in name_to_id:
            return name_to_id[candidate]

    name_matches = [
        (name, character_id)
        for name, character_id in name_to_id.items()
        if name and name in raw
    ]
    if name_matches:
        return max(name_matches, key=lambda item: len(item[0]))[1]

    id_matches = [character_id for character_id in known_ids if character_id in raw]
    return max(id_matches, key=len) if id_matches else ""


def _dialogue_element(
    character: str,
    text: str,
    parenthetical: str = "",
    emotion: str = "",
) -> dict | None:
    line = text.strip()
    if not character or not line:
        return None
    return {
        "type": "dialogue",
        "character": character,
        "parenthetical": parenthetical.strip(),
        "text": line,
        "emotion": emotion.strip(),
    }


def _dialogue_from_speaker_line(
    text: str,
    known_ids: set[str],
    name_to_id: dict[str, str],
    parenthetical: str = "",
    emotion: str = "",
) -> dict | None:
    match = re.match(r"^\s*[-—]?\s*(?P<speaker>[^:：\n]{1,40})\s*[:：]\s*(?P<line>.+)$", text)
    if not match:
        return None

    speaker = match.group("speaker").strip()
    note_match = re.search(r"[（(](?P<note>[^）)]{1,20})[）)]", speaker)
    note = parenthetical or (note_match.group("note").strip() if note_match else "")
    character = _resolve_character_reference(speaker, known_ids, name_to_id)
    return _dialogue_element(character, match.group("line"), note, emotion)


def _speaker_token_pattern(known_ids: set[str], name_to_id: dict[str, str]) -> str:
    tokens = sorted({*known_ids, *name_to_id.keys()}, key=len, reverse=True)
    return "|".join(re.escape(token) for token in tokens if token)


def _split_text_scene_elements(
    text: str,
    known_ids: set[str],
    name_to_id: dict[str, str],
) -> list[dict]:
    speaker_pattern = _speaker_token_pattern(known_ids, name_to_id)
    if not speaker_pattern:
        return [{"type": "action", "text": text}]

    pattern = re.compile(
        rf"(?P<speaker>{speaker_pattern})(?P<note>[（(][^）)]{{1,20}}[）)])?\s*[:：]"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return [{"type": "action", "text": text}]

    elements: list[dict] = []
    cursor = 0
    for index, match in enumerate(matches):
        prefix = text[cursor : match.start()].strip()
        if prefix:
            elements.append({"type": "action", "text": prefix})

        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        line = text[match.end() : end].strip()
        note = (match.group("note") or "").strip("（）()")
        character = _resolve_character_reference(match.group("speaker"), known_ids, name_to_id)
        dialogue = _dialogue_element(character, line, note)
        if dialogue:
            elements.append(dialogue)

        cursor = end

    suffix = text[cursor:].strip()
    if suffix:
        elements.append({"type": "action", "text": suffix})
    return elements or [{"type": "action", "text": text}]


def _normalize_scene_element(
    element: object, known_ids: set[str], name_to_id: dict[str, str]
) -> list[dict]:
    if isinstance(element, str):
        text = element.strip()
        return _split_text_scene_elements(text, known_ids, name_to_id) if text else []
    if not isinstance(element, dict):
        return []

    element_type = _normalize_element_type(element.get("type"))
    text = _first_text_by_keys(element, _ELEMENT_TEXT_KEYS)
    character = _resolve_character_reference(
        _first_text_by_keys(element, _ELEMENT_CHARACTER_KEYS),
        known_ids,
        name_to_id,
    )
    parenthetical = _as_text(element.get("parenthetical"))
    emotion = _as_text(element.get("emotion"))

    if element_type == "dialogue" or character:
        dialogue = _dialogue_element(character, text, parenthetical, emotion)
        if dialogue:
            return [dialogue]

        if text:
            parsed = _dialogue_from_speaker_line(
                text, known_ids, name_to_id, parenthetical, emotion
            )
            if parsed:
                return [parsed]

    if text:
        return _split_text_scene_elements(text, known_ids, name_to_id)
    return []


def _normalize_scene_elements(
    elements: object,
    known_ids: set[str],
    name_to_id: dict[str, str],
) -> list[dict]:
    normalized: list[dict] = []
    if isinstance(elements, list):
        for element in elements:
            resolved = _normalize_scene_element(element, known_ids, name_to_id)
            normalized.extend(resolved)
    return normalized


_DECISION_REASONS: dict[str, str] = {
    "action": "可见行为改写成动作行，推动场面前进。",
    "dialogue": "原文对白保留为推动冲突与信息交换的台词。",
    "subtext": "心理活动通过潜台词和反应间接表现，不直接搬成台词。",
    "scene_description": "环境、空间与氛围信息用于建立可拍摄的场景描述。",
}


def _decision_reason(target: str) -> str:
    return _DECISION_REASONS.get(target, _DECISION_REASONS["action"])


def _scene_summary_from_content(elements: list[dict], camera_hints: list[str]) -> str:
    for element in elements:
        text = _as_text(element.get("text"))
        if text:
            return text[:60]
    for hint in camera_hints:
        text = _as_text(hint)
        if text:
            return text[:60]
    return "本场内容待补充。"


def _decisions_from_elements(elements: list[dict]) -> list[dict]:
    decisions: list[dict] = []
    for element in elements:
        text = _as_text(element.get("text"))
        if not text:
            continue
        target = "dialogue" if element.get("type") == "dialogue" else "action"
        decisions.append(
            {
                "source_text": text,
                "target": target,
                "rendering": text,
                "reason": _decision_reason(target),
            }
        )
    return decisions


def _normalize_dramatization_decisions(
    decisions: object, elements: list[dict], summary: str
) -> list[dict]:
    valid_targets = set(_DECISION_REASONS)
    normalized: list[dict] = []
    if isinstance(decisions, list):
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            source_text = _as_text(decision.get("source_text"))
            rendering = _as_text(decision.get("rendering"))
            # 丢弃没有任何真实内容的决策残桩，避免用占位文本充数。
            if not source_text and not rendering:
                continue
            target = _as_text(decision.get("target")).lower()
            if target not in valid_targets:
                target = "action"
            source_text = source_text or rendering
            rendering = rendering or source_text
            reason = _as_text(decision.get("reason")) or _decision_reason(target)
            normalized.append(
                {
                    "source_text": source_text,
                    "target": target,
                    "rendering": rendering,
                    "reason": reason,
                }
            )
    if not normalized:
        normalized = _decisions_from_elements(elements)
    if not normalized:
        normalized.append(
            {
                "source_text": summary,
                "target": "action",
                "rendering": summary,
                "reason": _decision_reason("action"),
            }
        )
    return normalized


def _normalize_screenplay_scene_data(
    scenes: object,
    known_ids: set[str],
    name_to_id: dict[str, str],
    chapter_titles: list[str],
    location_names: dict[str, str] | None = None,
) -> list[dict]:
    """Repair common AI scene deviations before strict schema validation."""
    allowed_fields = Scene.model_fields.keys()
    location_names = location_names or {}
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
        # 模型有时直接用 global_state 里的地点 id（如 location-5）当地点名；映射回真名。
        location = location_names.get(location, location)
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

        camera_hints = _as_text_list(pruned.get("camera_hints"))
        pruned["camera_hints"] = camera_hints

        elements = _normalize_scene_elements(
            _collect_raw_elements(scene), known_ids, name_to_id
        )
        # 摘要优先取模型原值，否则从真实场景内容派生，避免出现占位式摘要。
        summary = _as_text(pruned.get("summary")) or _scene_summary_from_content(
            elements, camera_hints
        )
        if not elements:
            elements = [{"type": "action", "text": summary}]
        pruned["elements"] = elements

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
        pruned["dramatization_decisions"] = _normalize_dramatization_decisions(
            pruned.get("dramatization_decisions"), elements, summary
        )
        normalized.append(pruned)
    return normalized


def _format_validation_error(exc: ValidationError) -> str:
    first_error = exc.errors()[0] if exc.errors() else {"loc": (), "msg": str(exc)}
    location = ".".join(str(item) for item in first_error.get("loc", ())) or "root"
    return f"{location}: {first_error.get('msg', str(exc))}"


def _load_ai_screenplay_data(content: str) -> dict:
    try:
        data = loads_json_object(content)
    except ValueError as exc:
        raise ValueError(f"AI 全文转换失败：模型返回内容不是有效 JSON。{exc}") from exc

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

    def __init__(self) -> None:
        # 实例级列表：类级可变默认值会被所有实例共享。
        self.last_run_warnings: list[str] = []

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
        progress_cb=None,
        scene_cb=None,
        meta_cb=None,
    ) -> Screenplay:
        self.last_run_warnings = []
        style = _adaptation_style_profile(adaptation_type)
        global_state = extract_global_story_state(chapters)
        # 仅以全局状态表中已识别的真实人物作为说话人解析依据，杜绝转换阶段再次把
        # 副词、动作短语注册成新人物。
        known_names = {state.name for state in global_state.characters}
        chapter_slices: list[tuple[Chapter, list[SceneSlice]]] = []

        # 本地转换只要几百毫秒，用户几乎看不到中间态；仍然按章汇报，保证两条实现
        # 的回调行为一致——只接参数不上报会让调用方无法分辨"没进度"和"不支持"。
        total = max(1, len(chapters))

        def report(done: int, note: str) -> None:
            if progress_cb is not None:
                progress_cb(done, total, note)

        report(0, f"正在按本地规则拆分 {len(chapters)} 个章节的场景。")

        for chapter in chapters:
            scene_slices = _split_scene_slices(chapter.content)
            chapter_slices.append((chapter, scene_slices))
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text, known_names)
                inner_state = _inner_state_from_text(scene_slice.text, known_names)
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

        if meta_cb is not None:
            meta_cb(
                {
                    "title": title.strip() or "未命名改编",
                    "genre": genre.strip(),
                    "adaptation_type": adaptation_type,
                    "characters": [
                        {"id": character.id, "name": character.name} for character in characters
                    ],
                }
            )

        scenes: list[Scene] = []
        scene_index = 1
        for chapter_no, (chapter, scene_slices) in enumerate(chapter_slices, start=1):
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text, known_names)
                inner_state = _inner_state_from_text(scene_slice.text, known_names)
                action_text = _first_action_sentence(scene_slice.text) or _first_sentence(
                    scene_slice.text
                )
                elements: list[Action | Dialogue] = [
                    Action(type="action", text=action_text)
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
                        goal=_scene_goal(action_text, dialogue, inner_state),
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
                # 本地转换 300ms 就跑完，用户几乎看不到中间态；仍然接 scene_cb，
                # 保证两条实现的回调行为对称（只接参数不上报会让调用方无法分辨
                # "没有流式"和"不支持流式"）。
                if scene_cb is not None:
                    scene_cb(scenes[-1].model_dump(mode="json"))

            report(
                chapter_no,
                f"已生成前 {chapter_no}/{len(chapters)} 章的场景（累计 {len(scenes)} 个）",
            )

        report(total, "正在校验剧本结构")
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


def _compact_global_state(global_state: GlobalStoryState) -> dict:
    """A slim cross-chapter context for chunk prompts.

    The full ``global_state`` (character goals/arcs/consistency notes and long
    location/timeline descriptions) is resent on every chunk call and bloats the
    input. Character ids/names are already provided separately as the stable
    roster, and timeline context is now retrieved on demand per chunk (RAG),
    so only the location roster remains fixed context here.
    """
    return {
        "locations": [location.name for location in global_state.locations],
    }


def _enrich_global_state_from_profiles(
    global_state: GlobalStoryState, profiles: list[dict]
) -> None:
    """Fill placeholder arc / goal / traits from AI character profiles, by name.

    Names and appearance chapters stay authoritative (local), so cross-chapter
    consistency is unaffected; only the semantic fields the rule extractor leaves
    as "待作者进一步补充" get replaced when the model offers something meaningful.
    """
    by_name = {
        _as_text(profile.get("name")): profile
        for profile in profiles
        if isinstance(profile, dict) and _as_text(profile.get("name"))
    }
    for state in global_state.characters:
        profile = by_name.get(state.name)
        if not profile:
            continue
        key_change = _as_text(profile.get("key_change"))
        if key_change and key_change not in PROFILE_PLACEHOLDERS:
            state.arc = key_change
        goal = _as_text(profile.get("goal"))
        if goal and goal not in PROFILE_PLACEHOLDERS:
            state.goal = goal
        traits = [
            trait
            for trait in _as_text(profile.get("personality")).split("、")
            if trait and trait not in PROFILE_PLACEHOLDERS
        ]
        if traits:
            state.traits = traits


_CHUNK_CONVERSION_ATTEMPTS = 3


class AIConverter:
    """OpenAI-compatible LLM converter.

    The provider is intentionally configured by environment variables so the
    project is not tied to one vendor.

    Conversion runs in chapter-sized slices (the "分块转换" the project documents):
    the local ``global_state`` and a stable character roster are fixed context,
    and each LLM call only has to return the scenes for one bounded chunk. This
    keeps every response small enough to reduce truncated, unparseable JSON.
    """

    mode = "ai"

    def __init__(self, llm_client: LLMClient | None = None, client=None) -> None:
        self.llm_client = llm_client or LLMClient(client=client, usage_label="AI mode")
        self.last_run_warnings: list[str] = []

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
        progress_cb=None,
        scene_cb=None,
        meta_cb=None,
    ) -> Screenplay:
        # 每轮开头清空：实例被复用时，上一轮的告警不应泄漏到这一轮。
        self.last_run_warnings = []
        style = _adaptation_style_profile(adaptation_type)
        global_state = extract_global_story_state(chapters)

        characters = _normalize_screenplay_character_data(
            [_state_character_data(state) for state in global_state.characters]
        )
        known_ids = {character["id"] for character in characters}
        name_to_id = {
            character["name"]: character["id"]
            for character in characters
            if character.get("name")
        }

        # 先切分片段：纯文本操作、不走网络，所以能提前拿到总数用于进度反馈。
        chunk_specs: list[tuple[Chapter, str, int, int, int]] = []
        for chapter_no, chapter in enumerate(chapters, start=1):
            chapter_chunks = _chapter_text_chunks(
                chapter, limit=self.llm_client.chapter_chunk_chars
            )
            for chunk_index, chunk_text in enumerate(chapter_chunks, start=1):
                chunk_specs.append(
                    (chapter, chunk_text, chunk_index, len(chapter_chunks), chapter_no)
                )
        total = len(chunk_specs)

        def report(done: int, note: str) -> None:
            """向调用方汇报进度。可能从工作线程调用，实现需自行保证线程安全。"""
            if progress_cb is not None:
                progress_cb(done, total, note)

        # 每个任务附带按需检索出的前文备忘（只允许引用更早章节，防未来剧情泄漏），
        # 替代此前随章节数线性膨胀的全量 timeline 注入。
        report(0, f"已切分 {total} 个片段，正在建立前文检索索引。")
        knowledge = build_story_knowledge(
            chapters, global_state, mode="ai", llm_client=self.llm_client
        )
        retrieval_top_k = rag_top_k()
        tasks: list[tuple[Chapter, str, int, int, list[dict]]] = []
        # 检索在建线程池之前串行执行：embedding 模式下每次都要 embed 一次 query，
        # 这段耗时若不单独汇报就会表现为进度条长时间停在起点。
        for position, spec in enumerate(chunk_specs, start=1):
            chapter, chunk_text, chunk_index, chunk_count, chapter_no = spec
            retrieved = knowledge.search(
                chunk_text[:400],
                top_k=retrieval_top_k,
                before_chapter=chapter_no,
                kinds=("chunk", "event"),
            )
            tasks.append((chapter, chunk_text, chunk_index, chunk_count, retrieved))
            report(0, f"检索前文备忘 {position}/{total}")

        results: list[list[dict] | None] = [None] * len(tasks)
        # results[i] 为 None 有两种含义：还没跑完、或该片段失败。水位推进要能区分
        # 二者，否则一个失败片段会永久挡住后面所有场景的显示。
        finished: list[bool] = [False] * len(tasks)
        failures: list[str] = []
        ai_profiles: list[dict] = []
        processed = 0
        processed_lock = threading.Lock()

        # location_names 原先在收尾处计算，现在归一化提前到 flush 路径，需要上提。
        # global_state.locations 在建线程池前就已确定（后续的小传富集只改人物）。
        location_names = {location.id: location.name for location in global_state.locations}
        scenes: list[dict] = []
        # 连续前缀水位：results 按索引槽存放，水位之前的槽位都已成为最终值，
        # 因此可以边完成边 flush，并在 flush 时一次性定下 scene-N 编号——取代
        # 收尾时的全局重排，编号从出现那一刻起就不再变动。
        # 代价是队头阻塞：慢的片段 0 会挡住 1-3 的显示。4 worker 下这是有界的
        # 小停顿，换来编号稳定，值得。
        watermark = 0
        flush_lock = threading.Lock()

        def flush_ready() -> None:
            """把水位之后已完成的连续片段归一化、编号并逐场景上报。"""
            nonlocal watermark
            with flush_lock:
                while watermark < len(tasks) and finished[watermark]:
                    raw_scenes = results[watermark]
                    chapter_title = tasks[watermark][0].title
                    watermark += 1
                    if raw_scenes is None:
                        continue  # 该片段失败，跳过但不阻塞后续片段
                    for scene in _normalize_screenplay_scene_data(
                        raw_scenes, known_ids, name_to_id, [chapter_title], location_names
                    ):
                        scene["id"] = f"scene-{len(scenes) + 1}"
                        scenes.append(scene)
                        if scene_cb is None:
                            continue
                        # 逐场景校验：只决定这一场能不能流给前端，不改变收尾处
                        # 整篇校验的行为——某一场有问题不该拖累其它场景的显示。
                        try:
                            validated = Scene.model_validate(scene)
                        except ValidationError:
                            continue
                        scene_cb(validated.model_dump(mode="json"))

        def run_task(task_index: int) -> None:
            nonlocal processed
            chapter, chunk_text, chunk_index, chunk_count, retrieved = tasks[task_index]

            def notify_retry(attempt: int, reason: str) -> None:
                report(processed, f"片段 {task_index + 1}/{total} 第 {attempt} 次尝试（{reason}）")

            try:
                results[task_index] = self._convert_chapter_chunk(
                    chapter,
                    chunk_text,
                    chunk_index,
                    chunk_count,
                    global_state,
                    characters,
                    adaptation_type,
                    style,
                    retrieved,
                    notify_retry,
                )
            finally:
                # 先标记完成再推进水位：flush_ready 的 while 会把此刻能连成前缀的
                # 片段一次刷完，因此任意交错顺序都不会漏刷。
                finished[task_index] = True
                flush_ready()
                # 成功与失败都推进计数：进度反映"已处理"，失败数在收尾时单独汇报。
                # 计数必须在这里加锁自增，不能挂在下面的回收循环上——那个循环按
                # 提交顺序阻塞取结果，会让进度先滞后再跳变。
                with processed_lock:
                    processed += 1
                    done = processed
                report(done, f"已处理 {done}/{total} 个片段")

        def run_profiles() -> None:
            # 与片段并发跑：人物小传输出短（截断风险低），失败就保留本地占位，不影响出稿。
            report(processed, "正在并发提取人物小传")
            try:
                ai_profiles.extend(
                    AICharacterProfiler(llm_client=self.llm_client).extract(chapters)
                )
            except ValueError:
                pass

        if meta_cb is not None:
            # 名册在建线程池前就已确定：后续的小传富集只填 arc/goal/traits，不增删
            # 人物也不改名字，所以这份名册对流式显示就是最终值。
            meta_cb(
                {
                    "title": title.strip() or "未命名改编",
                    "genre": genre.strip(),
                    "adaptation_type": adaptation_type,
                    "characters": [
                        {"id": item["id"], "name": item.get("name", "")} for item in characters
                    ],
                }
            )

        max_workers = max(1, min(self.llm_client.max_concurrency, len(tasks) + 1))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(run_task, index): index for index in range(len(tasks))
            }
            profile_future = executor.submit(run_profiles)
            for future in future_to_index:
                try:
                    future.result()
                except ValueError as exc:
                    # 单个片段多次重试仍失败时跳过，避免一个片段（空响应 / 截断 /
                    # 内容审查）让整篇转换前功尽弃；只要还有其它片段成功即可出稿。
                    failures.append(str(exc))
            profile_future.result()

        if failures:
            report(total, f"{len(failures)} 个片段转换失败已跳过，正在整理其余场景")
            # 跳过的片段此前只出现在滚动的进度消息里，最终响应不带这个信息：
            # 用户拿到一份很薄的剧本却不知道为什么。这里让它进入结果。
            self.last_run_warnings.append(
                f"{len(failures)}/{total} 个片段转换失败已跳过，"
                f"剧本内容不完整。最后一个错误：{failures[-1][:160]}"
            )
        report(total, "正在归一化场景并校验剧本结构")

        # 用 AI 人物小传补全 global_state 的 arc/goal/性格，再据此重建顶层 characters。
        if ai_profiles:
            _enrich_global_state_from_profiles(global_state, ai_profiles)
            characters = _normalize_screenplay_character_data(
                [_state_character_data(state) for state in global_state.characters]
            )

        # 场景已在水位推进时归一化并编号（见 flush_ready）。这里兜底再刷一次：
        # 正常路径下最后完成的片段已把水位推到末尾，这一次是空操作。
        flush_ready()
        if not scenes:
            raise ValueError(
                failures[-1] if failures else "AI 全文转换失败：模型没有返回任何有效场景。"
            )

        resolved_title = title.strip() or "未命名改编"
        screenplay_data = {
            "schema_version": "1.0",
            "title": resolved_title,
            "genre": genre.strip(),
            "logline": f"以{adaptation_type}方式围绕《{resolved_title}》核心冲突展开的剧本初稿。",
            "adaptation_type": adaptation_type,
            "source": _source_info_from_chapters(chapters).model_dump(mode="json"),
            "global_state": global_state.model_dump(mode="json"),
            "characters": characters,
            "scenes": scenes,
        }
        return _validate_ai_screenplay_data(screenplay_data)

    def _convert_chapter_chunk(
        self,
        chapter: Chapter,
        chunk_text: str,
        chunk_index: int,
        chunk_count: int,
        global_state: GlobalStoryState,
        characters: list[dict],
        adaptation_type: AdaptationType,
        style: AdaptationStyleProfile,
        retrieved: list[dict] | None = None,
        notify_retry=None,
    ) -> list[dict]:
        roster = [{"id": item["id"], "name": item["name"]} for item in characters]
        memo = [
            {"chapter": hit.get("chapter", ""), "snippet": hit.get("snippet", "")}
            for hit in (retrieved or [])
        ]
        prompt = (
            "你是专业影视编剧。请只把【本章】中的【当前片段】小说改编成 Story2Script 的场景列表。"
            "只返回可被 json.loads 解析的 JSON 对象，形如 {\"scenes\": [...]}，"
            "不要 YAML、Markdown 或解释文字。\n"
            "重点：心理描写不能照搬，要外化为动作、对白 emotion 和 camera_hints；"
            "按时间、地点、人物进出、情节转折和冲突变化把当前片段拆成场景；"
            "当前片段只覆盖本章的一部分，不要补写片段外剧情；"
            "每个片段返回 1 到 3 个 scene，保持 JSON 精简完整，避免超长响应。\n"
            f"改编类型：{adaptation_type}\n"
            f"改编要求：{style.prompt_instruction}\n"
            "稳定人物表（characters 和 dialogue.character 只能引用这里的 id，不要新造人物）："
            f"{json.dumps(roster, ensure_ascii=False)}\n"
            "全局状态表是固定上下文，必须保持人物、地点和时间线跨章一致："
            f"{json.dumps(_compact_global_state(global_state), ensure_ascii=False)}\n"
            "相关前文备忘（按语义检索出的前文片段，仅用于保持剧情连续性，禁止复写其内容）："
            f"{json.dumps(memo, ensure_ascii=False)}\n"
            "每个 scene 必须包含 id, heading, int_ext, time_of_day, location, source_chapter, "
            "summary, goal, conflict, beat, subtext, characters, characters_present, props, "
            "dramatization_decisions, elements, camera_hints; "
            f"source_chapter 必须固定为 \"{chapter.title}\"; "
            "heading 使用类似 INT. LIBRARY - DAY 的 slug line，并与 int_ext/location/time_of_day 对齐; "
            "elements 是本场正文，必须包含动作行（type=action）和原文对白（type=dialogue，character 用人物 id），"
            "不能把对白只写进 dramatization_decisions 而不放进 elements; "
            "camera_hints 只放简短镜头/调度提示; "
            "dramatization_decisions 每条要给出真实的 source_text（取自原文）和 rendering（改写后文本），"
            "target 只能是 action、dialogue、subtext、scene_description; "
            "dialogue 可包含 emotion。\n\n"
            f"{DATA_FENCE_NOTICE}\n"
            f"本章标题：{chapter.title}\n"
            f"本章片段：第 {chunk_index}/{chunk_count} 段\n"
            f"本章片段原文：\n{chunk_text}\n（片段数据结束）"
        )
        # 空响应、超时、网络抖动、偶发非法 JSON 多为瞬时问题，重试几次往往能恢复。
        # 重试必须绕过响应缓存：HTTP 200 但 scenes 不合法的响应若被复用，重试会空转。
        last_error: str = "未知错误"
        backoff = self.llm_client.retry_backoff_seconds
        for _attempt in range(_CHUNK_CONVERSION_ATTEMPTS):
            if _attempt:
                # 退避后再试：网关超时（504）、限流（429）这类瞬时失败立刻重试往往
                # 仍然失败，还会加重上游负担。此前三次重试几乎在同一瞬间打完。
                delay = backoff[min(_attempt - 1, len(backoff) - 1)] if backoff else 0.0
                if notify_retry is not None:
                    # 重试此前完全不可见：一个片段静默重试三次，用户只看到进度条卡住。
                    waiting = f"，等待 {delay:g}s 后重试" if delay else ""
                    notify_retry(_attempt + 1, f"{last_error[:40]}{waiting}")
                if delay:
                    time.sleep(delay)
            try:
                content = self.llm_client.complete_json(
                    prompt,
                    use_cache=(_attempt == 0),
                    prompt_id=CONVERSION_CHUNK_PROMPT,
                )
                data = loads_json_object(content)
            except ValueError as exc:
                last_error = str(exc)
                if is_fatal_error(exc):
                    # 配置缺失或 4xx：重试多少次都一样，立即放弃，不白等退避。
                    break
                continue
            scenes = None
            if isinstance(data, dict):
                scenes = data.get("scenes")
            elif isinstance(data, list):
                scenes = data
            if isinstance(scenes, list):
                return scenes
            last_error = "片段未返回有效的 scenes 列表。"
        raise ValueError(
            f"AI 全文转换失败：章节《{chapter.title}》第 {chunk_index}/{chunk_count} 个片段"
            f"在重试 {_CHUNK_CONVERSION_ATTEMPTS} 次后仍失败。{last_error}"
        )


def get_converter(mode: str = "demo") -> Converter:
    if mode == "demo":
        return DemoConverter()
    if mode == "ai":
        return AIConverter()
    raise ValueError(f"Unsupported converter mode: {mode}")

