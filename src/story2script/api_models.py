from pydantic import BaseModel, Field

from .screenplay import Screenplay


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


class ConvertResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    mode: str


class ValidateYamlRequest(BaseModel):
    yaml_text: str = Field(min_length=1)


class ValidateYamlResponse(BaseModel):
    valid: bool
    message: str

