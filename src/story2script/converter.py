import json
import os
import re
from typing import Protocol

import httpx

from .parser import Chapter
from .screenplay import Action, Character, Dialogue, Scene, Screenplay, SourceInfo


class Converter(Protocol):
    mode: str

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        raise NotImplementedError


def _first_sentence(text: str, limit: int = 100) -> str:
    sentence = re.split(r"(?<=[。！？.!?])", text.strip(), maxsplit=1)[0].strip()
    return sentence[:limit] or "故事在沉默中展开。"


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


class DemoConverter:
    """Deterministic converter used for offline demos and repeatable tests."""

    mode = "demo"

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        character_names: list[str] = []
        chapter_dialogues: list[tuple[str, str] | None] = []
        chapter_inner_states: list[tuple[str, list[str], str, str, list[str]] | None] = []

        for chapter in chapters:
            dialogue = _dialogue_from_text(chapter.content)
            inner_state = _inner_state_from_text(chapter.content)
            chapter_dialogues.append(dialogue)
            chapter_inner_states.append(inner_state)
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
            )
            for index, name in enumerate(character_names, start=1)
        ]
        character_ids = {character.name: character.id for character in characters}

        scenes: list[Scene] = []
        for index, chapter in enumerate(chapters, start=1):
            dialogue = chapter_dialogues[index - 1]
            inner_state = chapter_inner_states[index - 1]
            elements: list[Action | Dialogue] = [
                Action(type="action", text=_first_sentence(chapter.content))
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
                    id=f"scene-{index}",
                    heading=f"INT. {chapter.title} - DAY",
                    source_chapter=chapter.title,
                    summary=_first_sentence(chapter.content, 60),
                    characters=scene_characters,
                    elements=elements,
                    camera_hints=camera_hints,
                )
            )

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
            "只返回符合 Story2Script Screenplay Schema 的 JSON，不要 Markdown。\n\n"
            f"标题：{title or '请根据内容拟定'}\n"
            f"类型：{genre or '请根据内容判断'}\n"
            "Schema 要点：schema_version, title, genre, logline, source, characters, scenes; "
            "scene 包含 elements 和 camera_hints; dialogue 可包含 emotion。\n\n"
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

