"""改编 Agent 的数据结构：决策、轨迹与运行结果。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


AgentRunStatus = Literal["completed", "budget_exhausted", "failed"]


class AgentAction(BaseModel):
    """一次工具调用决策：由 planner 返回、经注册表校验后执行。"""

    tool: str
    params: dict = {}


class AgentStep(BaseModel):
    """决策轨迹中的一步：思考、动作、观察值与耗时。"""

    step: int
    thought: str = ""
    action: AgentAction | None = None
    observation: dict = {}
    error: str = ""
    duration_ms: int = 0


class AgentRunResult(BaseModel):
    """一次 Agent 运行的完整结果（不含剧本全文，剧本另行传递）。"""

    status: AgentRunStatus
    goal: str = ""
    mode: str = "demo"
    steps_used: int = 0
    llm_calls: int = 0
    initial_summary: dict = {}
    final_summary: dict = {}
    trace: list[AgentStep] = []
    message: str = ""
    session_id: str = ""
