import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .parser import Chapter
from .screenplay import Action, Character, Dialogue, Scene, Screenplay, SourceInfo


@dataclass(frozen=True)
class SceneSlice:
    text: str
    break_reasons: list[str]


class Converter(Protocol):
    mode: str

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        raise NotImplementedError


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


class DemoConverter:
    """Deterministic converter used for offline demos and repeatable tests."""

    mode = "demo"

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        character_names: list[str] = []
        chapter_slices: list[tuple[Chapter, list[SceneSlice]]] = []

        for chapter in chapters:
            scene_slices = _split_scene_slices(chapter.content)
            chapter_slices.append((chapter, scene_slices))
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text)
                inner_state = _inner_state_from_text(scene_slice.text)
                if dialogue and dialogue[0] not in character_names:
                    character_names.append(dialogue[0])
                if inner_state and inner_state[0] not in character_names:
                    character_names.append(inner_state[0])

        characters = [
            Character(
                id=f"character-{index}",
                name=name,
                description="从原文对白中自动识别的角色。",
                motivation="待作者进一步补充。",
                arc="在场景目标与冲突中逐步显露变化。",
            )
            for index, name in enumerate(character_names, start=1)
        ]
        character_ids = {character.name: character.id for character in characters}

        scenes: list[Scene] = []
        scene_index = 1
        for chapter, scene_slices in chapter_slices:
            for scene_slice in scene_slices:
                dialogue = _dialogue_from_text(scene_slice.text)
                inner_state = _inner_state_from_text(scene_slice.text)
                elements: list[Action | Dialogue] = [
                    Action(type="action", text=_first_sentence(scene_slice.text))
                ]
                scene_characters: list[str] = []
                camera_hints: list[str] = []

                if dialogue:
                    character_id = character_ids[dialogue[0]]
                    scene_characters.append(character_id)
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
                    if character_id not in scene_characters:
                        scene_characters.append(character_id)
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

                scenes.append(
                    Scene(
                        id=f"scene-{scene_index}",
                        heading=f"INT. {chapter.title} - DAY",
                        source_chapter=chapter.title,
                        summary=_first_sentence(scene_slice.text, 60),
                        goal=_scene_goal(scene_slice.text, dialogue, inner_state),
                        conflict=_scene_conflict(
                            scene_slice.break_reasons,
                            dialogue,
                            inner_state,
                        ),
                        beat="、".join(scene_slice.break_reasons),
                        subtext=_scene_subtext(dialogue, inner_state),
                        characters=scene_characters,
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
            logline=f"围绕《{resolved_title}》核心冲突展开的剧本初稿。",
            source=SourceInfo(
                chapter_count=len(chapters),
                chapter_titles=[chapter.title for chapter in chapters],
            ),
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

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        if not self.api_key:
            raise ValueError("AI mode requires AI_API_KEY.")
        if not self.base_url:
            raise ValueError("AI mode requires AI_BASE_URL.")
        if not self.model:
            raise ValueError("AI mode requires AI_MODEL.")

        source_text = "\n\n".join(f"{chapter.title}\n{chapter.content}" for chapter in chapters)
        prompt = (
            "你是专业影视编剧。请将小说改编成结构化剧本 JSON。"
            "重点：小说心理描写不能原样照搬，要外化为动作、对白 emotion 和 camera_hints。"
            "剧本要按时间变化、地点变化、人物进出、情节转折和冲突变化拆成多个场景。"
            "只返回符合 Story2Script Screenplay Schema 的 JSON，不要 Markdown。\n\n"
            f"标题：{title or '请根据内容拟定'}\n"
            f"类型：{genre or '请根据内容判断'}\n"
            "Schema 要点：schema_version, title, genre, logline, source, characters, scenes; "
            "character 必须包含 arc; scene 必须包含 goal, conflict, beat, subtext, "
            "elements 和 camera_hints; dialogue 可包含 emotion。\n\n"
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
        return Screenplay.model_validate(json.loads(content))


def get_converter(mode: str = "demo") -> Converter:
    if mode == "demo":
        return DemoConverter()
    if mode == "ai":
        return AIConverter()
    raise ValueError(f"Unsupported converter mode: {mode}")

