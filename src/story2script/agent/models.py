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
    """决策轨迹中的一步：思考、动作、观察值与耗时。

    role 用于多智能体协作时标注这一步出自哪个角色；单体运行留空。
    """

    step: int
    thought: str = ""
    action: AgentAction | None = None
    observation: dict = {}
    error: str = ""
    duration_ms: int = 0
    role: str = ""


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
    role: str = ""


class AgentMessage(BaseModel):
    """协作消息：主管派单与专职回报都走这一条通道。"""

    seq: int
    sender: str
    recipient: str
    kind: str = "report"  # dispatch | report
    content: str = ""
    at: str = ""


class TeamRunResult(BaseModel):
    """一次多智能体协作的完整结果（剧本与报告另行传递）。"""

    status: AgentRunStatus
    goal: str = ""
    mode: str = "demo"
    rounds_used: int = 0
    llm_calls: int = 0
    initial_summary: dict = {}
    final_summary: dict = {}
    continuity_summary: dict = {}
    role_summaries: dict = {}
    trace: list[AgentStep] = []
    messages: list[AgentMessage] = []
    message: str = ""
    session_id: str = ""
