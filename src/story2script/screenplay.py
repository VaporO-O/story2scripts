import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


AdaptationType = Literal["短剧", "影视剧", "舞台剧", "广播剧", "分镜脚本"]
SUPPORTED_ADAPTATION_TYPES: tuple[AdaptationType, ...] = (
    "短剧",
    "影视剧",
    "舞台剧",
    "广播剧",
    "分镜脚本",
)
DEFAULT_ADAPTATION_TYPE: AdaptationType = "影视剧"


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


class GlobalCharacterState(StrictModel):
    id: str = Field(pattern=r"^character-[0-9]+$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    first_appearance: str = Field(min_length=1)
    appearance_chapters: list[str] = Field(min_length=1)
    traits: list[str] = Field(default_factory=list)
    goal: str
    arc: str = Field(min_length=1)
    consistency_note: str = Field(min_length=1)


class GlobalLocationState(StrictModel):
    id: str = Field(pattern=r"^location-[0-9]+$")
    name: str = Field(min_length=1)
    first_appearance: str = Field(min_length=1)
    appearance_chapters: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)


class TimelineEvent(StrictModel):
    id: str = Field(pattern=r"^event-[0-9]+$")
    order: int = Field(ge=1)
    chapter: str = Field(min_length=1)
    time_marker: str = ""
    summary: str = Field(min_length=1)


class GlobalStoryState(StrictModel):
    characters: list[GlobalCharacterState] = Field(default_factory=list)
    locations: list[GlobalLocationState] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def ids_are_unique(self) -> Self:
        character_ids = [character.id for character in self.characters]
        location_ids = [location.id for location in self.locations]
        event_ids = [event.id for event in self.timeline]

        if len(character_ids) != len(set(character_ids)):
            raise ValueError("全局状态表角色 id 不能重复")
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("全局状态表地点 id 不能重复")
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("全局状态表时间线事件 id 不能重复")
        return self


class Character(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    description: str
    motivation: str
    arc: str = Field(min_length=1, description="Character arc across the screenplay")


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


IntExt = Literal["INT.", "EXT."]
TimeOfDay = Literal["DAY", "NIGHT"]


class Scene(StrictModel):
    id: str = Field(pattern=r"^scene-[0-9]+$")
    heading: str = Field(
        min_length=1,
        description="Standard scene heading, e.g. INT. LIBRARY - NIGHT",
    )
    int_ext: IntExt = Field(description="Industry slug-line interior/exterior marker")
    time_of_day: TimeOfDay = Field(description="Industry slug-line time marker")
    location: str = Field(min_length=1, description="Playable scene location")
    source_chapter: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    goal: str = Field(min_length=1, description="Visible character goal in this scene")
    conflict: str = Field(min_length=1, description="Dramatic conflict that blocks the goal")
    beat: str = Field(min_length=1, description="Scene beat or turning point")
    subtext: str = Field(min_length=1, description="Unspoken pressure beneath action/dialogue")
    characters: list[str] = Field(description="Character ids in this scene")
    characters_present: list[str] = Field(description="Character ids visibly present in this scene")
    props: list[str] = Field(description="Production-relevant props used or mentioned in this scene")
    elements: list[SceneElement] = Field(min_length=1)
    camera_hints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def slug_line_matches_production_fields(self) -> Self:
        expected_prefix = f"{self.int_ext} "
        expected_suffix = f" - {self.time_of_day}"
        if not self.heading.startswith(expected_prefix):
            raise ValueError("heading 必须以 int_ext 开头")
        if self.location not in self.heading:
            raise ValueError("heading 必须包含 location")
        if not self.heading.endswith(expected_suffix):
            raise ValueError("heading 必须以 time_of_day 结尾")
        return self


class Screenplay(StrictModel):
    schema_version: Literal["1.0"]
    title: str = Field(min_length=1)
    genre: str
    adaptation_type: AdaptationType
    logline: str = Field(min_length=1)
    source: SourceInfo
    global_state: GlobalStoryState
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

        global_character_ids = {character.id for character in self.global_state.characters}
        unknown_global_characters = global_character_ids - known_characters
        if unknown_global_characters:
            raise ValueError(
                "全局状态表引用了不存在的角色："
                f"{', '.join(sorted(unknown_global_characters))}"
            )

        for character in self.global_state.characters:
            referenced_chapters = set(character.appearance_chapters)
            referenced_chapters.add(character.first_appearance)
            unknown_chapters = referenced_chapters - known_chapters
            if unknown_chapters:
                raise ValueError(
                    f"{character.id} 的全局角色状态引用了不存在的章节："
                    f"{', '.join(sorted(unknown_chapters))}"
                )

        for location in self.global_state.locations:
            referenced_chapters = set(location.appearance_chapters)
            referenced_chapters.add(location.first_appearance)
            unknown_chapters = referenced_chapters - known_chapters
            if unknown_chapters:
                raise ValueError(
                    f"{location.id} 的全局地点状态引用了不存在的章节："
                    f"{', '.join(sorted(unknown_chapters))}"
                )

        for event in self.global_state.timeline:
            if event.chapter not in known_chapters:
                raise ValueError(f"{event.id} 引用了不存在的时间线章节：{event.chapter}")

        for scene in self.scenes:
            if scene.source_chapter not in known_chapters:
                raise ValueError(f"{scene.id} 引用了不存在的来源章节：{scene.source_chapter}")

            referenced = set(scene.characters)
            referenced.update(scene.characters_present)
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
