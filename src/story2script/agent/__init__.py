"""改编 Agent：任务规划、上下文管理、长/短期记忆与受控工具调用。

单体代理（`AdaptationAgent`）之上还有多智能体协作（`AdaptationTeam`）：
审校 / 一致性 / 改编三个专职 Agent 共享黑板，由主管按状态派单。
"""

from .blackboard import Blackboard, SpecialistReport, SpecialistTask
from .core import AGENT_PROMPT_MARKER, AdaptationAgent, AgentRunOutcome
from .memory import AgentSessionStore, Scratchpad
from .models import (
    AgentAction,
    AgentMessage,
    AgentRunResult,
    AgentRunStatus,
    AgentStep,
    TeamRunResult,
)
from .specialists import (
    ROLE_ADAPTER,
    ROLE_CONTINUITY,
    ROLE_LABELS,
    ROLE_REVIEWER,
    build_specialists,
)
from .team import (
    FINISH_ROLE,
    SUPERVISOR,
    TEAM_PROMPT_MARKER,
    AdaptationTeam,
    TeamRunOutcome,
)
from .tools import AgentContext, AgentToolbox, build_toolbox

__all__ = [
    "AGENT_PROMPT_MARKER",
    "FINISH_ROLE",
    "ROLE_ADAPTER",
    "ROLE_CONTINUITY",
    "ROLE_LABELS",
    "ROLE_REVIEWER",
    "SUPERVISOR",
    "TEAM_PROMPT_MARKER",
    "AdaptationAgent",
    "AdaptationTeam",
    "AgentAction",
    "AgentContext",
    "AgentMessage",
    "AgentRunOutcome",
    "AgentRunResult",
    "AgentRunStatus",
    "AgentSessionStore",
    "AgentStep",
    "AgentToolbox",
    "Blackboard",
    "Scratchpad",
    "SpecialistReport",
    "SpecialistTask",
    "TeamRunOutcome",
    "TeamRunResult",
    "build_specialists",
    "build_toolbox",
]
