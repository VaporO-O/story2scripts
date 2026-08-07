"""评测数据集、指标和报告的数据结构。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..screenplay import AdaptationType, DEFAULT_ADAPTATION_TYPE


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DialogueAttribution(EvalModel):
    quote: str = Field(min_length=1)
    speaker: str = Field(min_length=1)


ContinuityFaultKind = Literal["speaker_absent", "character_absent", "unknown_location"]


class ContinuityFault(EvalModel):
    kind: ContinuityFaultKind
    scene_index: int = Field(default=0, ge=0)


class ExpectedAnnotations(EvalModel):
    chapter_titles: list[str] = Field(min_length=3)
    character_names: list[str] = Field(default_factory=list)
    dialogue_attributions: list[DialogueAttribution] = Field(default_factory=list)
    # 边界用“本章第几个文本单元之后切场”表示，避免污染公开 Screenplay Schema。
    scene_boundaries: dict[str, list[int]] = Field(default_factory=dict)
    continuity_faults: list[ContinuityFault] = Field(default_factory=list)

    @model_validator(mode="after")
    def annotations_are_consistent(self) -> Self:
        known = set(self.chapter_titles)
        unknown = set(self.scene_boundaries) - known
        if unknown:
            raise ValueError(f"场景边界引用了未知章节：{', '.join(sorted(unknown))}")
        for chapter, boundaries in self.scene_boundaries.items():
            if any(value < 1 for value in boundaries):
                raise ValueError(f"{chapter} 的场景边界必须是正整数。")
            if len(boundaries) != len(set(boundaries)):
                raise ValueError(f"{chapter} 的场景边界不能重复。")
        return self


class EvalCase(EvalModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    genre: str = "剧情"
    adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE
    novel_text: str = Field(min_length=1)
    expected: ExpectedAnnotations


class EvalDataset(EvalModel):
    version: str = Field(min_length=1)
    split: Literal["dev", "holdout"]
    description: str = ""
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> Self:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("评测数据集中的 case id 不能重复。")
        return self


class ScoreCounts(EvalModel):
    expected: int = 0
    predicted: int = 0
    correct: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


class AttributionMetrics(EvalModel):
    expected: int = 0
    matched: int = 0
    correct: int = 0
    accuracy: float = 0.0


class SourceMetrics(EvalModel):
    scene_boundaries: ScoreCounts
    continuity_probe: ScoreCounts


class OutputMetrics(EvalModel):
    schema_valid: bool
    chapter_title_accuracy: float
    characters: ScoreCounts
    dialogue_attribution: AttributionMetrics


class BehaviorMetrics(EvalModel):
    action_count: int = 0
    invalid_action_count: int = 0
    repeated_action_count: int = 0
    tool_legal_rate: float = 1.0
    repeated_action_rate: float = 0.0
    circuit_breaker_triggered: bool = False


class VariantMetrics(EvalModel):
    variant: Literal["fixed_pipeline", "single_agent", "multi_agent"]
    status: str
    goal_achieved: bool
    duration_ms: int
    schema_valid: bool
    initial_avg_score: float = 0.0
    final_avg_score: float = 0.0
    score_delta: float = 0.0
    pass_count: int = 0
    fail_count: int = 0
    steps_used: int = 0
    rounds_used: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float | None = None
    behavior: BehaviorMetrics
    output: OutputMetrics


class CaseReport(EvalModel):
    case_id: str
    title: str
    split: str
    conversion_duration_ms: int
    conversion_prompt_tokens: int = 0
    conversion_completion_tokens: int = 0
    conversion_warnings: list[str] = Field(default_factory=list)
    source: SourceMetrics
    variants: dict[str, VariantMetrics]


class GateResult(EvalModel):
    name: str
    path: str
    passed: bool
    actual: float | int | bool | None = None
    expected: str
    message: str = ""


class EvalReport(EvalModel):
    report_version: str = "1"
    generated_at: str
    git_commit: str = ""
    dataset_versions: list[str]
    splits: list[str]
    mode: Literal["demo", "ai"]
    model: str = ""
    threshold: float
    max_steps: int
    max_rounds: int
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    cases: list[CaseReport]
    summary: dict
    gates: list[GateResult] = Field(default_factory=list)


class BaselineGate(EvalModel):
    name: str
    path: str
    minimum: float | None = None
    maximum: float | None = None
    equals: float | int | bool | None = None

    @model_validator(mode="after")
    def exactly_one_constraint(self) -> Self:
        constraints = [self.minimum is not None, self.maximum is not None, self.equals is not None]
        if sum(constraints) != 1:
            raise ValueError("每条评测门禁必须且只能设置 minimum、maximum、equals 之一。")
        return self


class EvalBaseline(EvalModel):
    version: str = "1"
    dataset_versions: list[str]
    mode: Literal["demo", "ai"] = "demo"
    gates: list[BaselineGate] = Field(min_length=1)
