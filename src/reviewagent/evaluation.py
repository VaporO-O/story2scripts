"""Versioned evaluation for defect recall and false-positive rate."""

from __future__ import annotations

import json
import shutil
# Historical evaluation requires the Git CLI for isolated worktree lifecycle.
import subprocess  # nosec B404
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ReviewFinding, ReviewRequest, ToolName
from .runner import start_review
from .security import redact_output, validate_git_revision, validate_report_prefix
from .tools import SubprocessToolRunner


class _EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewEvalCase(_EvalModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    base_ref: str = Field(min_length=1)
    head_ref: str = Field(min_length=1)
    tools: list[ToolName] = Field(default_factory=lambda: ["pytest"], min_length=1)
    pytest_targets: list[str] = Field(default_factory=list)
    pytest_collect_only: bool = False
    pytest_import_mode: Literal["prepend", "append", "importlib"] = "prepend"
    expected_rules: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def lists_are_unique(self) -> Self:
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("Evaluation tools must not contain duplicates.")
        if len(self.expected_rules) != len(set(self.expected_rules)):
            raise ValueError("Expected rules must not contain duplicates.")
        return self


class ReviewEvalDataset(_EvalModel):
    version: str = Field(min_length=1)
    description: str = ""
    cases: list[ReviewEvalCase] = Field(min_length=2)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> Self:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case ids must not contain duplicates.")
        if not any(case.expected_rules for case in self.cases):
            raise ValueError("Evaluation dataset must contain a defect case.")
        if not any(not case.expected_rules for case in self.cases):
            raise ValueError("Evaluation dataset must contain a clean case.")
        return self


class ReviewEvalCaseReport(_EvalModel):
    case_id: str
    title: str
    status: Literal["completed", "failed"]
    duration_ms: int = Field(ge=0)
    expected_rules: list[str]
    predicted_rules: list[str]
    matched_rules: list[str]
    findings: list[ReviewFinding] = Field(default_factory=list)
    error: str = ""


class ReviewEvalSummary(_EvalModel):
    case_count: int
    completed_case_count: int
    failed_case_count: int
    expected_positive_count: int
    expected_negative_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    true_negative_count: int
    recall: float
    false_positive_rate: float


class ReviewEvalReport(_EvalModel):
    report_version: str = "1"
    generated_at: str
    dataset_version: str
    repo_path: str
    cases: list[ReviewEvalCaseReport]
    summary: ReviewEvalSummary


def load_review_dataset(path: str | Path) -> ReviewEvalDataset:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Review evaluation dataset not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid review evaluation JSON: {source}: {exc}") from exc
    return ReviewEvalDataset.model_validate(payload)


def _git(args: list[str], repo: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git") or "git"
    try:
        # Only fixed Git worktree operations and validated revisions reach this boundary.
        return subprocess.run(  # nosec B603
            [git, *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Git worktree command failed: {redact_output(str(exc))}") from exc


def _add_worktree(repo: Path, target: Path, head_ref: str, timeout: int) -> None:
    result = _git(["worktree", "add", "--detach", str(target), head_ref], repo, timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Unable to create evaluation worktree: {redact_output(detail)}")


def _remove_worktree(repo: Path, target: Path, timeout: int) -> None:
    result = _git(["worktree", "remove", "--force", str(target)], repo, timeout)
    if result.returncode != 0 and target.exists():
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Unable to remove evaluation worktree: {redact_output(detail)}")


def _run_case(
    case: ReviewEvalCase,
    repo: Path,
    timeout_seconds: int,
) -> ReviewEvalCaseReport:
    started = time.perf_counter()
    validate_git_revision(case.base_ref, "base_ref")
    validate_git_revision(case.head_ref, "head_ref")
    with tempfile.TemporaryDirectory(prefix=f"reviewagent-{case.id}-") as temp_dir:
        parent = Path(temp_dir)
        worktree = parent / "checkout"
        _add_worktree(repo, worktree, case.head_ref, timeout_seconds)
        try:
            request = ReviewRequest(
                repo_path=str(worktree),
                base_ref=case.base_ref,
                head_ref="HEAD",
                mode="demo",
                tools=case.tools,
                timeout_seconds=timeout_seconds,
                pytest_targets=case.pytest_targets,
                pytest_collect_only=case.pytest_collect_only,
                pytest_import_mode=case.pytest_import_mode,
            )
            run = start_review(
                request,
                checkpoint_path=parent / "checkpoint.sqlite3",
                thread_id=case.id,
            )
            findings = run.report.summary.findings
            failed_tools = [
                result.tool for result in run.report.tool_results if result.status == "failed"
            ]
            if failed_tools:
                return ReviewEvalCaseReport(
                    case_id=case.id,
                    title=case.title,
                    status="failed",
                    duration_ms=int((time.perf_counter() - started) * 1_000),
                    expected_rules=case.expected_rules,
                    predicted_rules=[],
                    matched_rules=[],
                    findings=findings,
                    error="Review tools failed: " + ", ".join(failed_tools),
                )
            predicted_rules = sorted({finding.rule_id for finding in findings})
            expected = set(case.expected_rules)
            matched = sorted(expected.intersection(predicted_rules))
            return ReviewEvalCaseReport(
                case_id=case.id,
                title=case.title,
                status="completed",
                duration_ms=int((time.perf_counter() - started) * 1_000),
                expected_rules=case.expected_rules,
                predicted_rules=predicted_rules,
                matched_rules=matched,
                findings=findings,
            )
        finally:
            _remove_worktree(repo, worktree, timeout_seconds)


def summarize_evaluation(cases: list[ReviewEvalCaseReport]) -> ReviewEvalSummary:
    expected_positive = sum(bool(case.expected_rules) for case in cases)
    expected_negative = len(cases) - expected_positive
    true_positive = sum(
        bool(case.expected_rules) and bool(case.matched_rules) for case in cases
    )
    false_negative = expected_positive - true_positive
    false_positive = sum(
        not case.expected_rules and bool(case.predicted_rules) for case in cases
    )
    true_negative = sum(
        case.status == "completed"
        and not case.expected_rules
        and not case.predicted_rules
        for case in cases
    )
    return ReviewEvalSummary(
        case_count=len(cases),
        completed_case_count=sum(case.status == "completed" for case in cases),
        failed_case_count=sum(case.status == "failed" for case in cases),
        expected_positive_count=expected_positive,
        expected_negative_count=expected_negative,
        true_positive_count=true_positive,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        true_negative_count=true_negative,
        recall=true_positive / expected_positive if expected_positive else 1.0,
        false_positive_rate=false_positive / expected_negative if expected_negative else 0.0,
    )


def run_review_evaluation(
    dataset: ReviewEvalDataset,
    *,
    repo_path: str | Path,
    timeout_seconds: int = 300,
) -> ReviewEvalReport:
    preparer = SubprocessToolRunner()
    probe = ReviewRequest(
        repo_path=str(repo_path),
        base_ref=dataset.cases[0].base_ref,
        head_ref=dataset.cases[0].head_ref,
        tools=["diff"],
        timeout_seconds=timeout_seconds,
        allow_historical_head=True,
    )
    repo = Path(preparer.prepare_request(probe).repo_path)
    reports: list[ReviewEvalCaseReport] = []
    for case in dataset.cases:
        started = time.perf_counter()
        try:
            reports.append(_run_case(case, repo, timeout_seconds))
        except (OSError, RuntimeError, ValueError) as exc:
            reports.append(
                ReviewEvalCaseReport(
                    case_id=case.id,
                    title=case.title,
                    status="failed",
                    duration_ms=int((time.perf_counter() - started) * 1_000),
                    expected_rules=case.expected_rules,
                    predicted_rules=[],
                    matched_rules=[],
                    error=redact_output(str(exc)),
                )
            )
    return ReviewEvalReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset_version=dataset.version,
        repo_path=str(repo),
        cases=reports,
        summary=summarize_evaluation(reports),
    )


def render_evaluation_markdown(report: ReviewEvalReport) -> str:
    summary = report.summary
    lines = [
        "# Code Review Agent Evaluation",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Dataset: `{report.dataset_version}`",
        f"- Recall: `{summary.recall:.3f}`",
        f"- False positive rate: `{summary.false_positive_rate:.3f}`",
        f"- Completed: `{summary.completed_case_count}/{summary.case_count}`",
        "",
        "| Case | Status | Expected | Predicted | Matched | Duration |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for case in report.cases:
        expected = ", ".join(case.expected_rules) or "clean"
        predicted = ", ".join(case.predicted_rules) or "clean"
        matched = ", ".join(case.matched_rules) or "-"
        lines.append(
            f"| {case.case_id} | {case.status} | {expected} | {predicted} | "
            f"{matched} | {case.duration_ms} ms |"
        )
        if case.error:
            lines.append(f"\n- `{case.case_id}` error: {case.error}\n")
    return "\n".join(lines).rstrip() + "\n"


def write_evaluation_reports(
    report: ReviewEvalReport,
    output_dir: str | Path,
    prefix: str = "reviewagent-latest",
) -> tuple[Path, Path]:
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    name = validate_report_prefix(prefix)
    json_path = target / f"{name}.json"
    markdown_path = target / f"{name}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_evaluation_markdown(report), encoding="utf-8")
    return json_path, markdown_path
