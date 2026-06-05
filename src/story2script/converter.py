import re

from .parser import Chapter
from .screenplay import Action, Character, Dialogue, Scene, Screenplay, SourceInfo


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


class DemoConverter:
    """Deterministic converter used for offline demos and repeatable tests."""

    def convert(self, chapters: list[Chapter], title: str = "", genre: str = "") -> Screenplay:
        character_names: list[str] = []
        chapter_dialogues: list[tuple[str, str] | None] = []

        for chapter in chapters:
            dialogue = _dialogue_from_text(chapter.content)
            chapter_dialogues.append(dialogue)
            if dialogue and dialogue[0] not in character_names:
                character_names.append(dialogue[0])

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
            elements: list[Action | Dialogue] = [
                Action(type="action", text=_first_sentence(chapter.content))
            ]
            scene_characters: list[str] = []

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

            scenes.append(
                Scene(
                    id=f"scene-{index}",
                    heading=f"INT. {chapter.title} - DAY",
                    source_chapter=chapter.title,
                    summary=_first_sentence(chapter.content, 60),
                    characters=scene_characters,
                    elements=elements,
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

