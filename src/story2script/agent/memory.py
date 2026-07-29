"""改编 Agent 的记忆：短期 Scratchpad（上下文管理）与长期会话存储。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..scene_review import ReviewReport
from ..screenplay import Screenplay
from ..yaml_export import screenplay_from_yaml, screenplay_to_yaml
from .models import AgentRunResult, AgentStep

SESSION_DIR_ENV = "AGENT_SESSION_DIR"
DEFAULT_SESSION_DIRNAME = ".agent_sessions"
_RECENT_FULL_STEPS = 4
_OBSERVATION_CHARS = 300


def _compact_params(params: dict) -> str:
    if not params:
        return ""
    parts = []
    for key, value in params.items():
        text = str(value)
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _step_brief(step: AgentStep) -> str:
    action = step.action.tool if step.action else "无动作"
    outcome = "error" if (step.error or "error" in step.observation) else "ok"
    return f"step{step.step}: {action}({_compact_params(step.action.params if step.action else {})}) -> {outcome}"


def _step_full(step: AgentStep) -> str:
    lines = [f"step{step.step} 思考：{step.thought}"]
    if step.action is not None:
        lines.append(f"  动作：{step.action.tool} 参数：{json.dumps(step.action.params, ensure_ascii=False)}")
    if step.error:
        lines.append(f"  错误：{step.error}")
    if step.observation:
        observation = json.dumps(step.observation, ensure_ascii=False)
        if len(observation) > _OBSERVATION_CHARS:
            observation = observation[:_OBSERVATION_CHARS] + "…"
        lines.append(f"  观察：{observation}")
    return "\n".join(lines)


class Scratchpad:
    """短期记忆：近几步保留全文，更早的压缩成一行，总长度有界。

    这是发给 planner 的唯一历史，保证 agent 循环的上下文不随步数无限膨胀。
    """

    def __init__(self, max_chars: int = 6000) -> None:
        self.max_chars = max_chars
        self._steps: list[AgentStep] = []

    def append(self, step: AgentStep) -> None:
        self._steps.append(step)

    def render(self) -> str:
        if not self._steps:
            return "（还没有历史步骤）"
        recent = self._steps[-_RECENT_FULL_STEPS:]
        earlier = self._steps[: -_RECENT_FULL_STEPS]
        lines = [_step_brief(step) for step in earlier] + [_step_full(step) for step in recent]
        while len(lines) > 1 and sum(len(line) + 1 for line in lines) > self.max_chars:
            lines.pop(0)
        text = "\n".join(lines)
        if len(text) > self.max_chars:
            text = text[-self.max_chars :]
        return text


class AgentSessionStore:
    """长期记忆：把一次运行的目标、轨迹、指标和剧本成果持久化到磁盘。"""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        raw = base_dir or os.getenv(SESSION_DIR_ENV) or Path.cwd() / DEFAULT_SESSION_DIRNAME
        self.base_dir = Path(raw).expanduser()

    def _path(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.json"

    def save(
        self,
        result: AgentRunResult,
        screenplay: Screenplay,
        report: ReviewReport | None = None,
    ) -> str:
        session_id = f"ag-{uuid4().hex[:8]}"
        payload = {
            "session_id": session_id,
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "goal": result.goal,
            "status": result.status,
            "mode": result.mode,
            "final_summary": result.final_summary,
            "result": result.model_dump(mode="json"),
            "yaml_text": screenplay_to_yaml(screenplay),
            "report": report.model_dump(mode="json") if report is not None else None,
        }
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path(session_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return session_id

    def load(self, session_id: str) -> dict:
        path = self._path(session_id)
        if not path.is_file():
            raise ValueError(f"会话不存在：{session_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = payload.get("report")
        return {
            "session_id": payload.get("session_id", session_id),
            "saved_at": payload.get("saved_at", ""),
            "goal": payload.get("goal", ""),
            "status": payload.get("status", ""),
            "result": AgentRunResult.model_validate(payload["result"]),
            "screenplay": screenplay_from_yaml(payload["yaml_text"]),
            "report": ReviewReport.model_validate(report) if report else None,
        }

    def list_sessions(self) -> list[dict]:
        if not self.base_dir.is_dir():
            return []
        sessions = []
        for path in self.base_dir.glob("ag-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sessions.append(
                {
                    "session_id": payload.get("session_id", path.stem),
                    "saved_at": payload.get("saved_at", ""),
                    "goal": payload.get("goal", ""),
                    "status": payload.get("status", ""),
                    "mode": payload.get("mode", ""),
                    "final_summary": payload.get("final_summary", {}),
                }
            )
        sessions.sort(key=lambda item: item["saved_at"], reverse=True)
        return sessions
