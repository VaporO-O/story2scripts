import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .parser import Chapter
from .screenplay import DEFAULT_ADAPTATION_TYPE
from .screenplay import Action
from .screenplay import AdaptationType
from .screenplay import Character
from .screenplay import Dialogue
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
    action_cue: str
    conflict_cue: str
    beat_cue: str
    subtext_cue: str
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
        action_cue="短剧节奏：动作直接切入冲突点。",
        conflict_cue="短剧冲突：提高阻力密度，保留强反转空间。",
        beat_cue="短剧节拍",
        subtext_cue="潜台词强调欲望、误会和反转压力。",
        production_hints=("节奏提示：场景尽快抛出钩子和反转点。",),
        arc_cue="在密集冲突和反转中快速暴露人物选择。",
        prompt_instruction="短剧：节奏快，冲突密集，每场保留钩子和强反转。",
    ),
    "影视剧": AdaptationStyleProfile(
        action_cue="影视剧调度：动作兼顾空间、人物反应和镜头感。",
        conflict_cue="影视剧冲突：用完整场景推进人物关系和信息变化。",
        beat_cue="影视剧节拍",
        subtext_cue="潜台词保留在动作、停顿和人物反应中。",
        production_hints=("镜头提示：建立空间关系后切入人物近景。",),
        arc_cue="在完整场景推进中逐步显露人物变化。",
        prompt_instruction="影视剧：场景完整，镜头感强，动作和人物反应清晰。",
    ),
    "舞台剧": AdaptationStyleProfile(
        action_cue="舞台提示：用走位、停顿和空间关系表现行动。",
        conflict_cue="舞台冲突：让人物在同一空间内形成可见对峙。",
        beat_cue="舞台节拍",
        subtext_cue="潜台词通过停顿、目光和舞台距离表现。",
        production_hints=("舞台调度：标注人物站位、进退和视线方向。",),
        arc_cue="在舞台走位和对峙关系中显露人物转变。",
        prompt_instruction="舞台剧：增加舞台提示、人物走位、停顿和空间对峙。",
    ),
    "广播剧": AdaptationStyleProfile(
        action_cue="声音提示：用环境音、动作声和旁白承接画面信息。",
        conflict_cue="广播剧冲突：通过声音距离、语气和沉默制造阻力。",
        beat_cue="声音节拍",
        subtext_cue="潜台词通过语气、呼吸、停顿和音效反差表现。",
        production_hints=("声音设计：环境音先行，关键动作配合音效。",),
        arc_cue="在声音表演、旁白和沉默中显露人物变化。",
        prompt_instruction="广播剧：强调音效、旁白、声音距离和声音表演。",
    ),
    "分镜脚本": AdaptationStyleProfile(
        action_cue="分镜提示：动作按画面推进，突出景别和构图。",
        conflict_cue="分镜冲突：用画面调度和景别变化呈现阻力。",
        beat_cue="分镜节拍",
        subtext_cue="潜台词通过构图、视线方向和画面留白表现。",
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
    r"(?:一|这|那)?(?:封|张|把|个|件|本|只|枚|串|部|台|盏|块|条|支|瓶|盒)"
    r"(?P<name>[^，。！？\n]{1,12})"
)
PROP_BOUNDARY_PATTERN = re.compile(
    r"(?:出现|落下|掉下|放在|拿起|递给|藏在|打开|关上|写着|留下|发现|看见|"
    r"说|问|喊|答道|低声道)"
)


def _adaptation_style_profile(adaptation_type: AdaptationType) -> AdaptationStyleProfile:
    return ADAPTATION_STYLE_PROFILES[adaptation_type]


def _first_sentence(text: str, limit: int = 100) -> str:
    match = re.search(r"^(.+?[。！？.!?][”\"]?)", text.strip(), re.DOTALL)
    sentence = match.group(1).strip() if match else text.strip()
    return sentence[:limit] or "故事在沉默中展开。"


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
    quote = re.search(r"[“\"]([^”\"]{2,100})[”\"]", text)
    if not quote:
        return None

    prefix = text[max(0, quote.start() - 12) : quote.start()]
    speaker_match = re.search(r"([\u4e00-\u9fff]{2,5})(?:说|问|喊|答道|低声道)[，,:：]?$", prefix)
    speaker = speaker_match.group(1) if speaker_match else "叙述者"
    return speaker, quote.group(1)


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


def _styled_action_text(text: str, style: AdaptationStyleProfile) -> str:
    return f"{style.action_cue} {text}"


def _styled_conflict(text: str, style: AdaptationStyleProfile) -> str:
    return f"{style.conflict_cue} {text}"


def _styled_beat(reasons: list[str], style: AdaptationStyleProfile) -> str:
    return f"{style.beat_cue}：{'、'.join(reasons)}"


def _styled_subtext(text: str, style: AdaptationStyleProfile) -> str:
    return f"{style.subtext_cue} {text}"


def _scene_time_of_day(text: str) -> TimeOfDay:
    return "NIGHT" if NIGHT_MARKER_PATTERN.search(text) else "DAY"


def _scene_location(text: str, chapter_title: str, global_state: GlobalStoryState) -> str:
    for location in global_state.locations:
        if location.name in text:
            return location.name
    return chapter_title


def _scene_int_ext(text: str, location: str) -> IntExt:
    return "EXT." if EXTERIOR_CUE_PATTERN.search(f"{location}\n{text}") else "INT."


def _clean_prop_name(raw_name: str) -> str:
    name = raw_name.strip("，。！？、：:；;（）() ")
    if "的" in name:
        name = name.rsplit("的", 1)[-1]
    boundary_match = PROP_BOUNDARY_PATTERN.search(name)
    if boundary_match and boundary_match.start() >= 1:
        name = name[: boundary_match.start()]
    return name.strip("，。！？、：:；;（）() ")[:12]


def _scene_props(text: str) -> list[str]:
    props: list[str] = []
    for match in PROP_PHRASE_PATTERN.finditer(text):
        name = _clean_prop_name(match.group("name"))
        if name and name not in props:
            props.append(name)
    return props


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
                        text=_styled_action_text(
                            _first_sentence(scene_slice.text),
                            style,
                        ),
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
                    character_name, actions, line, emotion, hints = inner_state
                    character_id = character_ids[character_name]
                    _append_character_id(scene_characters, character_id)
                    elements.extend(Action(type="action", text=action) for action in actions)
                    elements.append(
                        Dialogue(
                            type="dialogue",
                            character=character_id,
                            parenthetical="",
                            text=line,
                            emotion=emotion,
                        )
                    )
                    camera_hints.extend(hints)

                location = _scene_location(scene_slice.text, chapter.title, global_state)
                int_ext = _scene_int_ext(scene_slice.text, location)
                time_of_day = _scene_time_of_day(scene_slice.text)
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
                        conflict=_styled_conflict(
                            _scene_conflict(
                                scene_slice.break_reasons,
                                dialogue,
                                inner_state,
                            ),
                            style,
                        ),
                        beat=_styled_beat(scene_slice.break_reasons, style),
                        subtext=_styled_subtext(_scene_subtext(dialogue, inner_state), style),
                        characters=scene_characters,
                        characters_present=list(scene_characters),
                        props=_scene_props(scene_slice.text),
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
            source=SourceInfo(
                chapter_count=len(chapters),
                chapter_titles=[chapter.title for chapter in chapters],
            ),
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

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    @property
    def api_key(self) -> str:
        return os.getenv("AI_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.getenv("AI_BASE_URL", "").strip().rstrip("/")

    @property
    def model(self) -> str:
        return os.getenv("AI_MODEL", "").strip()

    @property
    def timeout_seconds(self) -> float:
        return float(os.getenv("AI_TIMEOUT_SECONDS", "120"))

    def convert(
        self,
        chapters: list[Chapter],
        title: str = "",
        genre: str = "",
        adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE,
    ) -> Screenplay:
        if not self.api_key:
            raise ValueError("AI mode requires AI_API_KEY.")
        if not self.base_url:
            raise ValueError("AI mode requires AI_BASE_URL.")
        if not self.model:
            raise ValueError("AI mode requires AI_MODEL.")

        style = _adaptation_style_profile(adaptation_type)
        global_state = extract_global_story_state(chapters)
        source_text = "\n\n".join(f"{chapter.title}\n{chapter.content}" for chapter in chapters)
        prompt = (
            "你是专业影视编剧。请将小说改编成结构化剧本 JSON。"
            "重点：小说心理描写不能原样照搬，要外化为动作、对白 emotion 和 camera_hints。"
            "剧本要按时间变化、地点变化、人物进出、情节转折和冲突变化拆成多个场景。"
            "只返回符合 Story2Script Screenplay Schema 的 JSON，不要 Markdown。\n\n"
            f"标题：{title or '请根据内容拟定'}\n"
            f"类型：{genre or '请根据内容判断'}\n"
            f"改编类型：{adaptation_type}\n"
            f"改编要求：{style.prompt_instruction}\n"
            "全局状态表是固定上下文，分块转换时必须保持人物姓名、性格、地点和时间线一致："
            f"{json.dumps(global_state.model_dump(mode='json'), ensure_ascii=False)}\n"
            "Schema 要点：schema_version, title, genre, logline, source, characters, scenes; "
            "顶层必须包含 adaptation_type 和 global_state; character 必须包含 arc; "
            "scene 必须包含 int_ext, time_of_day, location, characters_present, props, "
            "goal, conflict, beat, subtext, elements 和 camera_hints; "
            "heading 必须与 int_ext/location/time_of_day 对齐，使用类似 INT. LIBRARY - DAY 的 slug line; "
            "dialogue 可包含 emotion。\n\n"
            f"小说原文：\n{source_text}"
        )
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        screenplay_data = json.loads(content)
        screenplay_data["global_state"] = global_state.model_dump(mode="json")
        existing_characters = {
            character.get("id"): character for character in screenplay_data.setdefault("characters", [])
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

        screenplay = Screenplay.model_validate(screenplay_data)
        if screenplay.adaptation_type != adaptation_type:
            raise ValueError("AI output adaptation_type must match requested adaptation_type.")
        return screenplay


def get_converter(mode: str = "demo") -> Converter:
    if mode == "demo":
        return DemoConverter()
    if mode == "ai":
        return AIConverter()
    raise ValueError(f"Unsupported converter mode: {mode}")

