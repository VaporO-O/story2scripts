import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInfo(StrictModel):
    chapter_count: int = Field(ge=3, description="Number of source novel chapters")
    chapter_titles: list[str] = Field(min_length=3)

    @model_validator(mode="after")
    def chapter_count_matches_titles(self) -> Self:
        if self.chapter_count != len(self.chapter_titles):
            raise ValueError("chapter_count 必须与 chapter_titles 数量一致")
        return self


class Character(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    description: str
    motivation: str


class Dialogue(StrictModel):
    type: Literal["dialogue"]
    character: str = Field(description="Character id")
    parenthetical: str
    text: str = Field(min_length=1)
    emotion: str = ""


class Action(StrictModel):
    type: Literal["action"]
    text: str = Field(min_length=1)


SceneElement = Dialogue | Action


class Scene(StrictModel):
    id: str = Field(pattern=r"^scene-[0-9]+$")
    heading: str = Field(
        min_length=1,
        description="Standard scene heading, e.g. INT. LIBRARY - NIGHT",
    )
    source_chapter: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    characters: list[str] = Field(description="Character ids in this scene")
    elements: list[SceneElement] = Field(min_length=1)
    camera_hints: list[str] = Field(default_factory=list)


class Screenplay(StrictModel):
    schema_version: Literal["1.0"]
    title: str = Field(min_length=1)
    genre: str
    logline: str = Field(min_length=1)
    source: SourceInfo
    characters: list[Character]
    scenes: list[Scene] = Field(min_length=1)

    @model_validator(mode="after")
    def references_are_valid(self) -> Self:
        character_ids = [character.id for character in self.characters]
        scene_ids = [scene.id for scene in self.scenes]

        if len(character_ids) != len(set(character_ids)):
            raise ValueError("角色 id 不能重复")
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("场景 id 不能重复")

        known_characters = set(character_ids)
        known_chapters = set(self.source.chapter_titles)
        for scene in self.scenes:
            if scene.source_chapter not in known_chapters:
                raise ValueError(f"{scene.id} 引用了不存在的来源章节：{scene.source_chapter}")

            referenced = set(scene.characters)
            referenced.update(
                element.character for element in scene.elements if isinstance(element, Dialogue)
            )
            unknown = referenced - known_characters
            if unknown:
                raise ValueError(f"{scene.id} 引用了不存在的角色：{', '.join(sorted(unknown))}")

        return self


def screenplay_json_schema() -> dict:
    schema_path = Path(__file__).parents[2] / "schema" / "screenplay.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))
