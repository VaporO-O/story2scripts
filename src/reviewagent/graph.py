"""LangGraph orchestration for parallel review tools and human approval."""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .models import (
    ApprovalDecision,
    ReviewReport,
    ReviewRequest,
    ReviewSummary,
    ToolName,
    ToolResult,
)
from .security import redact_output
from .synthesis import ReviewSynthesizer
from .tools import ToolRunner, make_finding


class ReviewGraphState(TypedDict, total=False):
    request: dict[str, Any]
    thread_id: str
    tool_name: ToolName
    planned_tools: list[ToolName]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    draft_report: dict[str, Any]
    final_report: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_review_graph(
    tool_runner: ToolRunner,
    synthesizer: ReviewSynthesizer,
    *,
    checkpointer: Any = None,
):
    def plan(state: ReviewGraphState) -> dict[str, Any]:
        request = ReviewRequest.model_validate(state["request"])
        return {"planned_tools": list(request.tools)}

    def fan_out(state: ReviewGraphState) -> list[Send]:
        return [
            Send(
                "run_tool",
                {
                    "request": state["request"],
                    "thread_id": state["thread_id"],
                    "tool_name": tool,
                },
            )
            for tool in state["planned_tools"]
        ]

    def run_tool(state: ReviewGraphState) -> dict[str, Any]:
        request = ReviewRequest.model_validate(state["request"])
        tool = state["tool_name"]
        try:
            result = tool_runner.run(tool, request)
        except Exception as exc:  # Tool branches must report failure without losing peers.
            message = redact_output(str(exc) or type(exc).__name__)
            finding = make_finding(
                source=tool,
                severity="high",
                rule_id="TOOL_EXECUTION_FAILED",
                title=f"{tool} execution failed",
                message=message,
            )
            result = ToolResult(
                tool=tool,
                status="failed",
                error=message,
                findings=[finding],
            )
        return {"tool_results": [result.model_dump(mode="json")]}

    def synthesize(state: ReviewGraphState) -> dict[str, Any]:
        request = ReviewRequest.model_validate(state["request"])
        order = {name: index for index, name in enumerate(state["planned_tools"])}
        tool_results = sorted(
            [ToolResult.model_validate(row) for row in state.get("tool_results", [])],
            key=lambda row: order[row.tool],
        )
        summary = synthesizer.synthesize(request, tool_results)
        report = ReviewReport(
            generated_at=_now(),
            thread_id=state["thread_id"],
            repo_path=request.repo_path,
            base_ref=request.base_ref,
            head_ref=request.head_ref,
            mode=request.mode,
            status="awaiting_approval",
            planned_tools=list(state["planned_tools"]),
            tool_results=tool_results,
            summary=ReviewSummary.model_validate(summary),
        )
        return {"draft_report": report.model_dump(mode="json")}

    def approval_gate(state: ReviewGraphState) -> dict[str, Any]:
        draft = ReviewReport.model_validate(state["draft_report"])
        raw_decision = interrupt(
            {
                "kind": "review_approval",
                "thread_id": state["thread_id"],
                "report": draft.model_dump(mode="json"),
            }
        )
        decision = ApprovalDecision.model_validate(raw_decision)
        final = draft.model_copy(
            update={
                "status": "approved" if decision.approved else "rejected",
                "decision": decision,
            }
        )
        return {"final_report": final.model_dump(mode="json")}

    builder = StateGraph(ReviewGraphState)
    builder.add_node("plan", plan)
    builder.add_node("run_tool", run_tool)
    builder.add_node("synthesize", synthesize)
    builder.add_node("approval", approval_gate)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", fan_out, ["run_tool"])
    builder.add_edge("run_tool", "synthesize")
    builder.add_edge("synthesize", "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=checkpointer)
