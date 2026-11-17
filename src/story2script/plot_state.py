"""Versioned adaptation facts, kept separately from the novel's source facts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .logic_models import Operation, StoryLogic, ReasoningReport, Fact


class PlotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlotChange(PlotModel):
    change_id: str
    instruction: str
    character_id: str
    effective_scene_id: str
    operation: Operation
    target: str = ""
    item: str = ""
    destination: str = ""
    secret: str = ""
    relationship: Literal["", "ally", "enemy", "neutral"] = ""
    source_character: str = ""
    source_item: str = ""
    custom_rule: str = ""
    bindings: dict[str, str] = Field(default_factory=dict)
    requires: list[Fact] = Field(default_factory=list)
    event_time: Literal["scene_end"] = "scene_end"


class StoryFact(PlotModel):
    subject: str
    predicate: str
    value: str
    target: str = ""
    valid_from_scene_id: str
    source: Literal["user", "scene"] = "scene"
    revision_id: str = ""


class PlotContinuity(PlotModel):
    revision_id: str
    changes: list[PlotChange] = Field(default_factory=list)
    facts: list[StoryFact] = Field(default_factory=list)
    logic: StoryLogic | None = None


class SceneDependency(PlotModel):
    scene_id: str
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    depends_on_scene_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class PlannedScene(PlotModel):
    scene_id: str
    impact: Literal["direct", "indirect", "scan"]
    rewrite: bool
    reason: str = Field(min_length=1)
    replacement_beats: list[str] = Field(min_length=1)


class PlotIssue(PlotModel):
    scene_id: str
    kind: Literal["state", "causality", "timeline", "instruction", "structure"]
    message: str = Field(min_length=1)


class RevisionPlan(PlotModel):
    plan_id: str
    base_hash: str
    parent_revision_id: str = ""
    change: PlotChange
    scenes: list[PlannedScene]
    dependencies: list[SceneDependency]
    unresolved_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    estimated_calls: int
    max_calls: int
    timeout_seconds: int
    logic: StoryLogic | None = None
    baseline_reasoning: ReasoningReport | None = None
    intervention_reasoning: ReasoningReport | None = None


def screenplay_hash(screenplay) -> str:
    data = screenplay.model_dump(mode="json")
    for key in ("creative_brief", "structure", "editing"):
        if not data.get(key):
            data.pop(key, None)
    # Preserve hashes of saved versions created before custom events existed.
    continuity = data.get("plot_state") or {}
    logic = continuity.get("logic") or {}
    events = list(continuity.get("changes", []))
    events += [e for scene in logic.get("scenes", []) for e in scene["events"]]
    for event in events:
        for key in ("custom_rule", "bindings"):
            if not event.get(key):
                event.pop(key, None)
    for key in ("custom_properties", "custom_rules"):
        if not logic.get(key):
            logic.pop(key, None)
    canonical = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
