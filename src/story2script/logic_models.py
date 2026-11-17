"""Finite, typed story logic. No executable code is accepted from a model."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal[
    "death", "exit", "revive", "return", "transfer", "move", "learn", "relationship", "custom"
]
EntityKind = Literal["character", "item", "location", "secret"]
CoreEventKind = Literal[
    "death",
    "exit",
    "revive",
    "return",
    "transfer",
    "move",
    "learn",
    "relationship",
    "act",
    "use_item",
]


class LogicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(LogicModel):
    id: str = Field(min_length=1)
    kind: EntityKind
    name: str = Field(min_length=1)


class Fact(LogicModel):
    subject: str
    predicate: str = Field(
        pattern=r"^(alive|available|at|owner|knows|contains|relationship|custom:[a-z][a-z0-9_]{0,63})$"
    )
    value: str
    target: str = ""

    def key(self) -> tuple[str, str, str]:
        return self.subject, self.predicate, self.target


class Evidence(LogicModel):
    scene_id: str
    element_index: int = Field(ge=0)
    quote: str = Field(min_length=1)


class InitialFact(LogicModel):
    fact: Fact
    evidence: list[Evidence] = Field(default_factory=list)
    assumption: bool = False
    explanation: str = Field(min_length=1)


class EventArguments(LogicModel):
    actor: str
    target: str = ""
    item: str = ""
    destination: str = ""
    secret: str = ""
    relationship: Literal["", "ally", "enemy", "neutral"] = ""
    source_character: str = ""
    source_item: str = ""
    requires: list[Fact] = Field(default_factory=list)


class RuleStep(EventArguments):
    kind: CoreEventKind


class Event(EventArguments):
    id: str = Field(min_length=1)
    kind: Literal[
        "death",
        "exit",
        "revive",
        "return",
        "transfer",
        "move",
        "learn",
        "relationship",
        "act",
        "use_item",
        "custom",
    ]
    custom_rule: str = ""
    bindings: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class CustomProperty(LogicModel):
    id: str = Field(pattern=r"^custom:[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    subject_kind: EntityKind
    target_kind: Literal["", "character", "item", "location", "secret"] = ""
    values: list[str] = Field(min_length=1, max_length=64)
    symmetric: bool = False
    # Optional automatic restrictions for character actions or item use.
    usable_values: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_domain(self):
        if len(set(self.values)) != len(self.values) or any(
            not v or v.startswith("$") for v in self.values
        ):
            raise ValueError("自定义状态取值必须非空、唯一，且不能使用参数引用。")
        if self.symmetric and (not self.target_kind or self.subject_kind != self.target_kind):
            raise ValueError("对称状态要求相同类型的主体与对象。")
        if self.usable_values and (
            self.subject_kind not in ("character", "item")
            or self.target_kind
            or not set(self.usable_values) <= set(self.values)
        ):
            raise ValueError("行动/使用限制只用于角色或物品的单体状态，允许值必须属于状态域。")
        return self


class CustomRule(LogicModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    # actor is an implicit character parameter, always bound from Event.actor.
    parameters: dict[str, EntityKind] = Field(default_factory=dict)
    active_roles: list[str] = Field(default_factory=lambda: ["actor"])
    requires: list[Fact] = Field(min_length=1, max_length=64)
    steps: list[RuleStep] = Field(default_factory=list, max_length=16)
    effects: list[Fact] = Field(default_factory=list, max_length=64)
    lock_effects: bool = False

    @model_validator(mode="after")
    def has_effect(self):
        if not self.steps and not self.effects:
            raise ValueError("自定义事件至少包含一个状态变化或内置步骤。")
        if any(not f.predicate.startswith("custom:") for f in self.effects):
            raise ValueError(
                "自定义效果只能修改 custom: 状态；生死、归属、知情等必须使用内置步骤。"
            )
        return self


class ElementCoverage(LogicModel):
    element_index: int = Field(ge=0)
    event_ids: list[str] = Field(default_factory=list)
    context_only: bool = False
    explanation: str = Field(min_length=1)


class SceneLogic(LogicModel):
    scene_id: str
    events: list[Event]
    coverage: list[ElementCoverage]


class StoryLogic(LogicModel):
    version: Literal["1"] = "1"
    entities: list[Entity]
    initial_facts: list[InitialFact]
    scenes: list[SceneLogic]
    # Server-captured source of confirmed opening facts; rewriting must not erase
    # their citations or turn later events into opening facts.
    initial_source: dict[str, list[str]] = Field(default_factory=dict)
    initial_source_hash: str = ""
    custom_properties: list[CustomProperty] = Field(default_factory=list, max_length=64)
    custom_rules: list[CustomRule] = Field(default_factory=list, max_length=64)


class RequirementCheck(LogicModel):
    expected: Fact
    actual: str | None = None
    status: Literal["true", "false", "unknown"]
    established_by: str = ""
    allowed_values: list[str] = Field(default_factory=list)


class LogicIssue(LogicModel):
    scene_id: str = ""
    event_id: str = ""
    code: str
    message: str
    checks: list[RequirementCheck] = Field(default_factory=list)


class ProofStep(LogicModel):
    scene_id: str
    event_id: str
    rule: str
    status: Literal["applied", "blocked"]
    checks: list[RequirementCheck]
    effects: list[Fact]
    causes: list[str]
    substeps: list["ProofStep"] = Field(default_factory=list)


class ReasoningReport(LogicModel):
    engine_version: Literal["1"] = "1"
    valid: bool
    issues: list[LogicIssue]
    steps: list[ProofStep]
    final_facts: list[Fact]
    dependencies: dict[str, list[str]]
    scanned_scene_ids: list[str]
    assumptions: list[InitialFact]


OPERATION_LABELS = {
    "death": "死亡",
    "exit": "离场",
    "revive": "复活",
    "return": "回归",
    "transfer": "物品转移",
    "move": "地点移动",
    "learn": "获知秘密",
    "relationship": "关系变化",
    "custom": "自定义事件",
}
