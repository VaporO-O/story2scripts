"""Optional authoring metadata, independent of screenplay and rule execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreativeBrief(CreativeModel):
    template_id: str = "faithful"
    template_version: int = Field(default=1, ge=1)
    name: str = Field(default="忠实改编", min_length=1, max_length=80)
    tone: str = Field(default="克制自然", max_length=500)
    pacing: Literal["balanced", "fast", "slow"] = "balanced"
    fidelity: Literal["strict", "balanced", "creative"] = "strict"
    instructions: str = Field(default="", max_length=3000)


class SceneGroup(CreativeModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["act", "episode"]
    title: str = Field(min_length=1, max_length=100)
    scene_ids: list[str] = Field(min_length=1)
    target_minutes: float | None = Field(default=None, gt=0, le=300)


class EditingState(CreativeModel):
    status: Literal["needs_check", "checked"] = "needs_check"
    based_on_hash: str
    dirty_scene_ids: list[str] = Field(default_factory=list)
    note: str = ""
