"""协作黑板：多个专职 Agent 的共享工作区与消息通道。

黑板持有唯一一份剧本与分析结论，专职 Agent 只通过它读写状态、通过消息流
互相告知进展；主管据 `digest()` 决定下一步派单。

`digest()` 刻意做成有界视图（问题取前几条、消息取最近几条），与 Scratchpad
同一思路：发给 LLM 的状态不随协作轮次膨胀。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..continuity import ContinuityFinding, summarize_findings
from ..scene_review import ReviewReport
from ..screenplay import Screenplay
from .models import AgentMessage, AgentStep

_DIGEST_FINDINGS = 5
_DIGEST_MESSAGES = 8
_DIGEST_FAILING = 5


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class SpecialistTask:
    """主管派给某个专职 Agent 的一次任务。"""

    role: str
    instruction: str = ""
    scene_ids: list[str] = field(default_factory=list)


@dataclass
class SpecialistReport:
    """专职 Agent 的一次回报。"""

    role: str
    summary: str
    steps: list[AgentStep] = field(default_factory=list)
    observation: dict = field(default_factory=dict)
    llm_calls: int = 0
    changed_screenplay: bool = False


class Blackboard:
    """线程安全的共享状态。"""

    def __init__(self, screenplay: Screenplay, goal: str = "", threshold: float = 7.0) -> None:
        self._lock = threading.Lock()
        self.screenplay = screenplay
        self.goal = goal
        self.threshold = threshold
        self.report: ReviewReport | None = None
        self.continuity_findings: list[ContinuityFinding] = []
        self.messages: list[AgentMessage] = []
        self.role_summaries: dict[str, dict] = {}
        self.initial_summary: dict = {}
        self._seq = 0

    # ------------------------------------------------------------------ 写入

    def post(self, sender: str, recipient: str, content: str, kind: str = "report") -> AgentMessage:
        with self._lock:
            self._seq += 1
            message = AgentMessage(
                seq=self._seq,
                sender=sender,
                recipient=recipient,
                kind=kind,
                content=content,
                at=_now_iso(),
            )
            self.messages.append(message)
            return message

    def set_screenplay(self, screenplay: Screenplay) -> None:
        with self._lock:
            self.screenplay = screenplay

    def set_report(self, report: ReviewReport) -> None:
        with self._lock:
            self.report = report
            if not self.initial_summary:
                self.initial_summary = dict(report.summary)

    def set_continuity_findings(self, findings: list[ContinuityFinding]) -> None:
        with self._lock:
            self.continuity_findings = list(findings)

    def record_role_summary(self, role: str, summary: dict) -> None:
        with self._lock:
            self.role_summaries[role] = summary

    # ------------------------------------------------------------------ 读取

    def failing_scene_ids(self) -> list[str]:
        with self._lock:
            if self.report is None:
                return []
            failing = [item for item in self.report.machine.values() if item.verdict == "fail"]
        failing.sort(key=lambda item: (item.total, item.scene_id))
        return [item.scene_id for item in failing]

    def high_severity_findings(self) -> list[ContinuityFinding]:
        with self._lock:
            return [item for item in self.continuity_findings if item.severity == "high"]

    def review_summary(self) -> dict:
        with self._lock:
            return dict(self.report.summary) if self.report is not None else {}

    def continuity_summary(self) -> dict:
        with self._lock:
            return summarize_findings(self.continuity_findings)

    def digest(self) -> dict:
        """给主管（含 LLM）看的有界状态视图。"""
        with self._lock:
            report = self.report
            findings = list(self.continuity_findings)
            messages = list(self.messages)
            role_summaries = dict(self.role_summaries)
            scene_count = len(self.screenplay.scenes)

        failing = []
        if report is not None:
            items = [item for item in report.machine.values() if item.verdict == "fail"]
            items.sort(key=lambda item: (item.total, item.scene_id))
            failing = [
                {
                    "scene_id": item.scene_id,
                    "total": item.total,
                    "suggested_operation": item.suggested_operation,
                }
                for item in items[:_DIGEST_FAILING]
            ]
        return {
            "scene_count": scene_count,
            "review": dict(report.summary) if report is not None else {},
            "reviewed": report is not None,
            "failing": failing,
            "continuity": summarize_findings(findings),
            "continuity_checked": "continuity" in role_summaries,
            "top_findings": [
                {
                    "scene_id": item.scene_id,
                    "kind": item.kind,
                    "severity": item.severity,
                    "detail": item.detail[:120],
                }
                for item in findings[:_DIGEST_FINDINGS]
            ],
            "recent_messages": [
                {"seq": item.seq, "sender": item.sender, "content": item.content[:120]}
                for item in messages[-_DIGEST_MESSAGES:]
            ],
            "role_summaries": role_summaries,
        }

    def snapshot(self) -> dict:
        """完整快照，用于结果与前端展示。"""
        with self._lock:
            return {
                "review_summary": dict(self.report.summary) if self.report is not None else {},
                "continuity_summary": summarize_findings(self.continuity_findings),
                "continuity_findings": [
                    item.model_dump(mode="json") for item in self.continuity_findings
                ],
                "messages": [item.model_copy() for item in self.messages],
                "role_summaries": dict(self.role_summaries),
            }
