"""改编 Agent 的工具注册表：受控工具调用层。

工具是既有工作台函数的薄封装，操作 AgentContext 内的剧本/报告状态，
返回紧凑观察值（不回传全文）。非法工具或参数不抛异常，而是把错误作为
观察值返回，交给 planner 自我修正。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..scene_review import ReviewReport, review_scenes_report
from ..scene_rewrite import OPERATION_MESSAGES
from ..scene_rewrite import rewrite_scene as perform_scene_rewrite
from ..screenplay import Screenplay
from .models import AgentAction

FINISH_TOOL = "finish"
_ELEMENT_PREVIEW_COUNT = 3


@dataclass
class AgentContext:
    """Agent 运行期的可变状态：工具执行的读写目标。"""

    screenplay: Screenplay
    mode: str = "demo"
    client: object | None = None
    threshold: float = 7.0
    report: ReviewReport | None = None
    initial_summary: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    params_schema: dict
    handler: Callable[..., dict]


def _failing_entries(report: ReviewReport) -> list[dict]:
    failing = [result for result in report.machine.values() if result.verdict == "fail"]
    failing.sort(key=lambda result: (result.total, result.scene_id))
    return [
        {
            "scene_id": result.scene_id,
            "total": result.total,
            "suggested_operation": result.suggested_operation,
            "feedback": result.feedback,
            "issues": result.issues[:3],
        }
        for result in failing
    ]


def _tool_review(ctx: AgentContext, scene_ids: list[str] | None = None) -> dict:
    report = review_scenes_report(
        ctx.screenplay,
        mode=ctx.mode,
        client=ctx.client,
        threshold=ctx.threshold,
        scene_ids=scene_ids or None,
    )
    ctx.report = report
    return {"summary": report.summary, "failing": _failing_entries(report)}


def _tool_list_failing(ctx: AgentContext) -> dict:
    if ctx.report is None:
        return {"error": "还没有审校报告，请先调用 review_screenplay。"}
    return {"failing": _failing_entries(ctx.report)}


def _render_element(element) -> str:
    if element.type == "dialogue":
        return f"{element.character}：{element.text}"
    return f"[动作] {element.text}"


def _tool_get_scene(ctx: AgentContext, scene_id: str) -> dict:
    for scene in ctx.screenplay.scenes:
        if scene.id == scene_id:
            return {
                "id": scene.id,
                "heading": scene.heading,
                "summary": scene.summary,
                "goal": scene.goal,
                "conflict": scene.conflict,
                "characters": scene.characters,
                "element_count": len(scene.elements),
                "first_elements": [
                    _render_element(element)
                    for element in scene.elements[:_ELEMENT_PREVIEW_COUNT]
                ],
            }
    return {"error": f"未找到场景：{scene_id}"}


def _tool_rewrite(
    ctx: AgentContext,
    scene_id: str,
    operation: str,
    feedback: str = "",
    character_id: str = "",
    tone: str = "更克制",
) -> dict:
    if operation not in OPERATION_MESSAGES:
        supported = "、".join(OPERATION_MESSAGES)
        return {"error": f"不支持的重写操作：{operation}。可选：{supported}"}
    updated, message = perform_scene_rewrite(
        ctx.screenplay,
        scene_id,
        operation,  # type: ignore[arg-type]
        character_id=character_id,
        tone=tone,
        mode=ctx.mode,  # type: ignore[arg-type]
        client=ctx.client,
        feedback=feedback,
    )
    ctx.screenplay = updated
    return {
        "scene_id": scene_id,
        "operation": operation,
        "message": message,
        "hint": "评分已过期，请调用 review_screenplay 复评。",
    }


def _tool_compare(ctx: AgentContext) -> dict:
    current = ctx.report.summary if ctx.report is not None else {}
    return {"initial": ctx.initial_summary, "current": current}


def build_toolbox(ctx: AgentContext) -> "AgentToolbox":
    tools = [
        AgentTool(
            name="review_screenplay",
            description="机审全部或指定场景，按四项标准打分并更新审校报告。",
            params_schema={"scene_ids": "可选，list[str]，只复评这些场景"},
            handler=_tool_review,
        ),
        AgentTool(
            name="list_failing_scenes",
            description="列出当前不达标场景（按分数升序），含建议操作与审校意见。",
            params_schema={},
            handler=_tool_list_failing,
        ),
        AgentTool(
            name="get_scene",
            description="查看单个场景的紧凑内容（heading、目标、冲突、前几条元素）。",
            params_schema={"scene_id": "必填，如 scene-1"},
            handler=_tool_get_scene,
        ),
        AgentTool(
            name="rewrite_scene",
            description=(
                "对指定场景执行局部重写。operation 可选："
                + "、".join(OPERATION_MESSAGES)
                + "。feedback 传入审校意见可引导重写方向。"
            ),
            params_schema={
                "scene_id": "必填",
                "operation": "必填，六种操作之一",
                "feedback": "可选，审校意见",
                "character_id": "可选，adjust_character_voice 时指定人物",
                "tone": "可选，语气",
            },
            handler=_tool_rewrite,
        ),
        AgentTool(
            name="compare_scores",
            description="对比初始与当前审校汇总（avg_score/pass_count/fail_count）。",
            params_schema={},
            handler=_tool_compare,
        ),
        AgentTool(
            name=FINISH_TOOL,
            description="结束任务并给出总结。达标、无可改进空间或目标完成时调用。",
            params_schema={"summary": "可选，一句话总结做了什么、达到什么效果"},
            handler=lambda ctx, summary="": {"summary": summary},
        ),
    ]
    return AgentToolbox(ctx, tools)


class AgentToolbox:
    def __init__(self, ctx: AgentContext, tools: list[AgentTool]) -> None:
        self.ctx = ctx
        self._tools = {tool.name: tool for tool in tools}

    def describe(self) -> str:
        lines = []
        for tool in self._tools.values():
            params = (
                "；".join(f"{key}：{desc}" for key, desc in tool.params_schema.items())
                or "无参数"
            )
            lines.append(f"- {tool.name}：{tool.description}（参数：{params}）")
        return "\n".join(lines)

    def execute(self, action: AgentAction) -> dict:
        tool = self._tools.get(action.tool)
        if tool is None:
            supported = "、".join(self._tools)
            return {"error": f"不支持的工具：{action.tool}。可选：{supported}"}
        unknown = set(action.params) - set(tool.params_schema)
        if unknown:
            return {"error": f"工具 {action.tool} 不接受参数：{'、'.join(sorted(unknown))}"}
        try:
            return tool.handler(self.ctx, **action.params)
        except TypeError as exc:
            return {"error": f"工具 {action.tool} 参数不完整：{exc}"}
        except ValueError as exc:
            return {"error": str(exc)}
