"""Structured contracts for review requests, tools, and reports."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolName = Literal["diff", "ruff", "pytest", "bandit"]
Severity = Literal["critical", "high", "medium", "low", "info"]
ReviewMode = Literal["demo", "ai"]
ReviewStatus = Literal["awaiting_approval", "approved", "rejected"]

DEFAULT_TOOLS: tuple[ToolName, ...] = ("diff", "ruff", "pytest", "bandit")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewRequest(ReviewModel):
    repo_path: str
    base_ref: str
    head_ref: str = "HEAD"
    mode: ReviewMode = "demo"
    tools: list[ToolName] = Field(default_factory=lambda: list(DEFAULT_TOOLS), min_length=1)
    max_output_chars: int = Field(default=50_000, ge=2_000, le=250_000)
    timeout_seconds: int = Field(default=300, ge=5, le=3_600)
    allow_historical_head: bool = False
    pytest_targets: list[str] = Field(default_factory=list)
    pytest_collect_only: bool = False
    pytest_import_mode: Literal["prepend", "append", "importlib"] = "prepend"

    @model_validator(mode="after")
    def tools_are_unique(self) -> Self:
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("Review tools must not contain duplicates.")
        for target in self.pytest_targets:
            path_text = target.split("::", 1)[0]
            path = Path(path_text)
            if (
                not path_text
                or target.startswith("-")
                or "\x00" in target
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError("pytest targets must be safe paths relative to the repository.")
        return self


class ReviewFinding(ReviewModel):
    fingerprint: str = Field(min_length=8)
    source: ToolName
    severity: Severity
    rule_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    file: str = ""
    line: int = Field(default=0, ge=0)
    evidence: str = ""


class ToolResult(ReviewModel):
    tool: ToolName
    status: Literal["passed", "findings", "failed", "skipped"]
    command: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    output: str = ""
    error: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)


class ReviewSummary(ReviewModel):
    verdict: Literal["clean", "changes_requested"]
    overview: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class ApprovalDecision(ReviewModel):
    approved: bool
    comment: str = Field(default="", max_length=4_000)


class ReviewReport(ReviewModel):
    review_version: str = "1"
    generated_at: str
    thread_id: str
    repo_path: str
    base_ref: str
    head_ref: str
    mode: ReviewMode
    status: ReviewStatus
    planned_tools: list[ToolName]
    tool_results: list[ToolResult]
    summary: ReviewSummary
    decision: ApprovalDecision | None = None


class ReviewRunResult(ReviewModel):
    thread_id: str
    awaiting_approval: bool
    report: ReviewReport
