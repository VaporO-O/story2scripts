from typing import Literal

from pydantic import Field

from .plot_state import PlotModel, RevisionPlan, PlotIssue, StoryFact
from .screenplay import Scene, Screenplay
from .logic_models import Operation, StoryLogic, ReasoningReport, SceneLogic


class PlotPlanRequest(PlotModel):
    yaml_text: str = Field(min_length=1, max_length=2_000_000)
    novel_text: str = Field(default="", max_length=2_000_000)
    instruction: str = Field(min_length=1, max_length=4000)
    character_id: str = ""
    scene_id: str
    operation: Operation = "death"
    target: str = ""
    item: str = ""
    destination: str = ""
    secret: str = ""
    relationship: Literal["", "ally", "enemy", "neutral"] = ""
    source_character: str = ""
    source_item: str = ""
    custom_rule: str = ""
    bindings: dict[str, str] = Field(default_factory=dict)
    max_calls: int = Field(default=80, ge=3, le=500)
    timeout_seconds: int = Field(default=1200, ge=30, le=7200)


class PlotJobRequest(PlotModel):
    plan_id: str
    yaml_text: str = Field(min_length=1, max_length=2_000_000)
    confirm_logic: bool = False


class SceneChange(PlotModel):
    scene_id: str
    reason: str
    before: Scene
    after: Scene


class ScriptRevision(PlotModel):
    revision_id: str
    parent_revision_id: str
    plan_id: str
    base_hash: str
    result_hash: str
    status: Literal["candidate", "needs_review", "adopted", "rolled_back"]
    base_screenplay: Screenplay
    screenplay: Screenplay
    plan: RevisionPlan
    changes: list[SceneChange]
    issues: list[PlotIssue]
    scanned_scene_ids: list[str]
    calls: int
    duration_seconds: float
    yaml_text: str
    reasoning: ReasoningReport | None = None


class PlotJobStatus(PlotModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    stage: str
    message: str
    result: ScriptRevision | None = None
    error: str = ""


class PlotVersionRequest(PlotModel):
    yaml_text: str = Field(min_length=1, max_length=2_000_000)


class PlotVersionResponse(PlotModel):
    revision_id: str
    screenplay: Screenplay
    yaml_text: str
    message: str


class SceneReview(PlotModel):
    scene_id: str
    issues: list[PlotIssue]
    facts: list[StoryFact]
    logic: SceneLogic | None = None


class RevisionAudit(PlotModel):
    scanned_scene_ids: list[str]
    issues: list[PlotIssue]


class LogicCheckRequest(PlotModel):
    yaml_text: str = Field(min_length=1, max_length=2_000_000)
    logic: StoryLogic


class LogicPlanUpdate(PlotModel):
    logic: StoryLogic
