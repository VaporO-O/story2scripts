"""Persistent run/resume facade for the review graph."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .graph import build_review_graph
from .models import ApprovalDecision, ReviewReport, ReviewRequest, ReviewRunResult
from .security import validate_thread_id
from .synthesis import (
    DeterministicSynthesizer,
    LLMReviewSynthesizer,
    ReviewSynthesizer,
)
from .tools import SubprocessToolRunner, ToolRunner


DEFAULT_CHECKPOINT = Path(".reviewagent") / "review.sqlite3"


def _default_synthesizer(mode: str) -> ReviewSynthesizer:
    if mode == "demo":
        return DeterministicSynthesizer()

    # Keep the graph core independent. Only this composition layer knows the
    # existing Story2Script client and therefore preserves its metrics/cache/redaction.
    from story2script.llm_client import LLMClient, loads_json_object

    client = LLMClient(usage_label="Code review agent")
    return LLMReviewSynthesizer(client, json_loader=loads_json_object)


@contextmanager
def _persistent_graph(
    checkpoint_path: str | Path,
    tool_runner: ToolRunner,
    synthesizer: ReviewSynthesizer,
) -> Iterator[object]:
    path = Path(checkpoint_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        saver = SqliteSaver(connection)
        saver.setup()
        yield build_review_graph(tool_runner, synthesizer, checkpointer=saver)
    finally:
        connection.close()


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": validate_thread_id(thread_id)}}


def _result_from_state(thread_id: str, values: dict, awaiting: bool) -> ReviewRunResult:
    report_payload = values.get("final_report") or values.get("draft_report")
    if not report_payload:
        raise RuntimeError("Review graph completed without producing a report.")
    return ReviewRunResult(
        thread_id=thread_id,
        awaiting_approval=awaiting,
        report=ReviewReport.model_validate(report_payload),
    )


def start_review(
    request: ReviewRequest,
    *,
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    thread_id: str | None = None,
    tool_runner: ToolRunner | None = None,
    synthesizer: ReviewSynthesizer | None = None,
    validate_repository: bool = True,
) -> ReviewRunResult:
    resolved_thread = validate_thread_id(thread_id or f"review-{uuid.uuid4().hex[:12]}")
    runner = tool_runner or SubprocessToolRunner()
    prepared = request
    if validate_repository and isinstance(runner, SubprocessToolRunner):
        prepared = runner.prepare_request(request)
    selected_synthesizer = synthesizer or _default_synthesizer(prepared.mode)

    with _persistent_graph(checkpoint_path, runner, selected_synthesizer) as graph:
        config = _config(resolved_thread)
        snapshot = graph.get_state(config)  # type: ignore[attr-defined]
        if snapshot.values:
            raise ValueError(f"Review thread already exists: {resolved_thread}")
        values = graph.invoke(  # type: ignore[attr-defined]
            {
                "request": prepared.model_dump(mode="json"),
                "thread_id": resolved_thread,
                "tool_results": [],
            },
            config=config,
        )
        awaiting = bool(values.get("__interrupt__"))
        if not awaiting:
            snapshot = graph.get_state(config)  # type: ignore[attr-defined]
            awaiting = "approval" in snapshot.next
        return _result_from_state(resolved_thread, values, awaiting)


def resume_review(
    *,
    checkpoint_path: str | Path,
    thread_id: str,
    decision: ApprovalDecision,
    tool_runner: ToolRunner | None = None,
    synthesizer: ReviewSynthesizer | None = None,
) -> ReviewRunResult:
    resolved_thread = validate_thread_id(thread_id)
    runner = tool_runner or SubprocessToolRunner()
    selected_synthesizer = synthesizer or DeterministicSynthesizer()

    with _persistent_graph(checkpoint_path, runner, selected_synthesizer) as graph:
        config = _config(resolved_thread)
        snapshot = graph.get_state(config)  # type: ignore[attr-defined]
        if not snapshot.values:
            raise ValueError(f"Unknown review thread: {resolved_thread}")
        if snapshot.values.get("final_report"):
            raise ValueError(f"Review thread is already complete: {resolved_thread}")
        if "approval" not in snapshot.next:
            raise ValueError(f"Review thread is not waiting for approval: {resolved_thread}")

        values = graph.invoke(  # type: ignore[attr-defined]
            Command(resume=decision.model_dump(mode="json")),
            config=config,
        )
        return _result_from_state(resolved_thread, values, awaiting=False)
