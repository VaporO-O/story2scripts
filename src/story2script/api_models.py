from pydantic import BaseModel, Field

from .screenplay import DEFAULT_ADAPTATION_TYPE
from .screenplay import AdaptationType
from .screenplay import Screenplay
from .scene_rewrite import SceneRewriteOperation


class ChapterPreviewRequest(BaseModel):
    novel_text: str = Field(min_length=1)


class ChapterPreviewItem(BaseModel):
    index: int
    title: str
    character_count: int
    preview: str


class ChapterPreviewResponse(BaseModel):
    chapter_count: int
    chapters: list[ChapterPreviewItem]


class ConvertRequest(BaseModel):
    novel_text: str = Field(min_length=1)
    title: str = ""
    genre: str = ""
    adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE
    mode: str = "demo"


class ConvertResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    mode: str
    adaptation_type: AdaptationType


class ValidateYamlRequest(BaseModel):
    yaml_text: str = Field(min_length=1)


class ValidateYamlResponse(BaseModel):
    valid: bool
    message: str


class SceneRewriteRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    operation: SceneRewriteOperation
    character_id: str = ""
    tone: str = "更克制"


class SceneRewriteResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    scene_id: str
    operation: SceneRewriteOperation
    message: str


class ExampleNovelResponse(BaseModel):
    title: str
    genre: str
    novel_text: str


class CharacterProfileRequest(BaseModel):
    novel_text: str = Field(min_length=1)


class CharacterProfile(BaseModel):
    name: str
    role: str
    personality: str
    goal: str
    relationships: list[str]
    appearance_chapters: list[str]
    key_change: str


class CharacterProfileResponse(BaseModel):
    profiles: list[CharacterProfile]

