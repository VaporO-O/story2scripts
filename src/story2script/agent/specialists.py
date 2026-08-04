"""三个专职 Agent：审校 / 一致性 / 改编。

统一实现同一接口（`role` + `run(blackboard, task) -> SpecialistReport`），由主管
按黑板状态派单。审校与一致性是确定性分析器（不需要"规划下一步"，硬套 LLM 循环
只会凭空造出决策空间并多花 token）；改编专职内部复用既有 `AdaptationAgent`，
其自主循环原样保留。
"""

from __future__ import annotations

import time
from typing import Protocol

from ..continuity import check_continuity, summarize_findings
from ..scene_review import review_scenes_report
from .blackboard import Blackboard, SpecialistReport, SpecialistTask
from .core import AdaptationAgent
from .models import AgentAction, AgentStep

ROLE_REVIEWER = "reviewer"
ROLE_CONTINUITY = "continuity"
ROLE_ADAPTER = "adapter"

ROLE_LABELS = {
    ROLE_REVIEWER: "审校",
    ROLE_CONTINUITY: "一致性",
    ROLE_ADAPTER: "改编",
}


class Specialist(Protocol):
    role: str

    def run(self, blackboard: Blackboard, task: SpecialistTask) -> SpecialistReport: ...


def _analysis_step(role: str, tool: str, thought: str, observation: dict, elapsed: float) -> AgentStep:
    return AgentStep(
        step=1,
        thought=thought,
        action=AgentAction(tool=tool, params={}),
        observation=observation,
        duration_ms=int(elapsed * 1000),
        role=role,
    )


class ReviewSpecialist:
    """审校专职：按四项标准给场景打分，把报告写回黑板。"""

    role = ROLE_REVIEWER

    def __init__(self, mode: str = "demo", client=None, threshold: float | None = None) -> None:
        self.mode = mode
        self.client = client
        self.threshold = threshold

    def run(self, blackboard: Blackboard, task: SpecialistTask) -> SpecialistReport:
        started = time.perf_counter()
        report = review_scenes_report(
            blackboard.screenplay,
            mode=self.mode,
            client=self.client,
            threshold=self.threshold if self.threshold is not None else blackboard.threshold,
            scene_ids=task.scene_ids or None,
        )
        blackboard.set_report(report)
        summary = dict(report.summary)
        failing = blackboard.failing_scene_ids()
        observation = {"summary": summary, "failing": failing[:5]}
        text = (
            f"机审完成：均分 {summary.get('avg_score', 0)}，"
            f"{summary.get('pass_count', 0)} 个通过 / {summary.get('fail_count', 0)} 个未通过。"
        )
        blackboard.record_role_summary(self.role, summary)
        return SpecialistReport(
            role=self.role,
            summary=text,
            steps=[
                _analysis_step(
                    self.role,
                    "review_screenplay",
                    task.instruction or "对全部场景执行机审打分。",
                    observation,
                    time.perf_counter() - started,
                )
            ],
            observation=observation,
        )


class ContinuitySpecialist:
    """一致性专职：把剧本与全局状态表比对，找跨章矛盾。"""

    role = ROLE_CONTINUITY

    def __init__(self, mode: str = "demo", client=None) -> None:
        self.mode = mode
        self.client = client

    def run(self, blackboard: Blackboard, task: SpecialistTask) -> SpecialistReport:
        started = time.perf_counter()
        findings = check_continuity(
            blackboard.screenplay,
            blackboard.screenplay.global_state,
            mode=self.mode,
            client=self.client,
        )
        blackboard.set_continuity_findings(findings)
        summary = summarize_findings(findings)
        observation = {
            "summary": summary,
            "top": [
                {
                    "scene_id": item.scene_id,
                    "kind": item.kind,
                    "severity": item.severity,
                    "detail": item.detail[:100],
                }
                for item in findings[:5]
            ],
        }
        text = (
            f"一致性检查完成：{summary['total']} 个问题"
            f"（严重 {summary['high']} / 中 {summary['medium']} / 轻 {summary['low']}）。"
            if findings
            else "一致性检查完成：未发现跨章矛盾。"
        )
        blackboard.record_role_summary(self.role, summary)
        return SpecialistReport(
            role=self.role,
            summary=text,
            steps=[
                _analysis_step(
                    self.role,
                    "check_continuity",
                    task.instruction or "比对剧本与全局状态表，检查跨章一致性。",
                    observation,
                    time.perf_counter() - started,
                )
            ],
            observation=observation,
        )


class AdapterSpecialist:
    """改编专职：复用自主改编代理，按主管的任务修复场景。"""

    role = ROLE_ADAPTER

    def __init__(
        self,
        mode: str = "demo",
        client=None,
        max_steps: int | None = None,
        threshold: float | None = None,
        knowledge=None,
    ) -> None:
        self.mode = mode
        self.client = client
        self.max_steps = max_steps
        self.threshold = threshold
        self.knowledge = knowledge

    def _goal_text(self, blackboard: Blackboard, task: SpecialistTask) -> str:
        if task.instruction:
            return task.instruction
        findings = blackboard.high_severity_findings()
        if findings:
            details = "；".join(item.detail for item in findings[:2])
            return f"修正不达标场景，同时注意这些一致性问题：{details}"
        return "把不达标的场景改写到通过机审。"

    def run(self, blackboard: Blackboard, task: SpecialistTask) -> SpecialistReport:
        agent = AdaptationAgent(
            mode=self.mode,
            client=self.client,
            max_steps=self.max_steps,
            threshold=self.threshold if self.threshold is not None else blackboard.threshold,
        )
        outcome = agent.run(
            blackboard.screenplay,
            goal=self._goal_text(blackboard, task),
            knowledge=self.knowledge,
        )
        blackboard.set_screenplay(outcome.screenplay)
        if outcome.report is not None:
            blackboard.set_report(outcome.report)

        steps = []
        for step in outcome.result.trace:
            tagged = step.model_copy(update={"role": self.role})
            steps.append(tagged)
        summary = dict(outcome.result.final_summary)
        blackboard.record_role_summary(self.role, summary)
        return SpecialistReport(
            role=self.role,
            summary=f"改编完成（{outcome.result.status}）：{outcome.result.message}",
            steps=steps,
            observation={"summary": summary, "steps_used": outcome.result.steps_used},
            llm_calls=outcome.result.llm_calls,
            changed_screenplay=True,
        )


def build_specialists(
    mode: str = "demo",
    client=None,
    threshold: float | None = None,
    max_steps_per_agent: int | None = None,
    knowledge=None,
) -> dict[str, Specialist]:
    return {
        ROLE_REVIEWER: ReviewSpecialist(mode=mode, client=client, threshold=threshold),
        ROLE_CONTINUITY: ContinuitySpecialist(mode=mode, client=client),
        ROLE_ADAPTER: AdapterSpecialist(
            mode=mode,
            client=client,
            max_steps=max_steps_per_agent,
            threshold=threshold,
            knowledge=knowledge,
        ),
    }
