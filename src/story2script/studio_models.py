from typing import Literal

from pydantic import Field, model_validator
from typing import Any

from .creative_models import CreativeModel, CreativeBrief, SceneGroup
from .screenplay import Scene, Screenplay


class ScriptRequest(CreativeModel):
    yaml_text: str = Field(min_length=1, max_length=2_000_000)


class ProjectionRequest(ScriptRequest):
    scene_id: str = ""
    boundary: Literal["start", "end"] = "end"


class DraftRequest(CreativeModel):
    screenplay: Screenplay
    draft_id: str = ""
    expected_version: int = Field(default=0, ge=0)
    base_yaml: str = ""


class EditRequest(ScriptRequest):
    scene: Scene | None = None
    structure: list[SceneGroup] | None = None
    creative_brief: CreativeBrief | None = None


class StructureRequest(ScriptRequest):
    kind: Literal["act", "episode"] = "act"
    count: int = Field(default=3, ge=1, le=100)
    target_minutes: float | None = Field(default=None, gt=0, le=300)
    mode: Literal["balanced", "ai"] = "balanced"


class StructureProposal(CreativeModel):
    groups: list[SceneGroup]


class ExportRequest(ScriptRequest):
    format: Literal["txt", "html", "docx"]
    include_appendix: bool = True


class EmotionRequest(ScriptRequest):
    mode: Literal["annotated", "ai"] = "annotated"
    scene_ids: list[str] = Field(default_factory=list)


class EmotionNote(CreativeModel):
    scene_id: str
    element_index: int = Field(ge=0)
    character_id: str
    quote: str = Field(min_length=1)
    emotion: str = Field(min_length=1, max_length=60)
    rationale: str = Field(default="", max_length=500)


class EmotionAnalysis(CreativeModel):
    notes: list[EmotionNote]


class RecheckRequest(ScriptRequest):
    mode: Literal["rules", "ai"] = "rules"


STUDIO_REQUESTS = {
    "structure": StructureRequest,
    "emotions": EmotionRequest,
    "recheck": RecheckRequest,
}


class StudioJobRequest(CreativeModel):
    operation: Literal["structure", "emotions", "recheck"]
    parameters: dict[str, Any]
    max_calls: int = Field(default=80, ge=1, le=500)
    timeout_seconds: int = Field(default=1200, ge=30, le=7200)

    @model_validator(mode="after")
    def typed_parameters(self):
        typed = STUDIO_REQUESTS[self.operation].model_validate(self.parameters)
        if typed.mode != "ai":
            raise ValueError("后台创作任务仅用于 AI 操作，本地操作使用原接口。")
        self.parameters = typed.model_dump(mode="json")
        return self


class StudioJobResult(CreativeModel):
    operation: Literal["structure", "emotions", "recheck"]
    base_hash: str
    payload: dict[str, Any]
    calls: int
    duration_seconds: float


class StudioJobStatus(CreativeModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    stage: str
    message: str
    error: str = ""
    result: StudioJobResult | None = None
    attempts: int = 0
    max_attempts: int = 3
    calls: int = 0
    max_calls: int = 0
    elapsed_seconds: float = 0
    timeout_seconds: int = 0
