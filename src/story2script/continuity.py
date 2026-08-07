"""跨章节一致性校验：把剧本与全局状态表比对，找出连续性矛盾。

项目此前只有一致性事实的**提取**（`story_state.extract_global_story_state`）
与提示词注入，没有任何**校验**环节。本模块补上这一层，供一致性 Agent 使用。

四条本地规则全部确定性、零依赖，且只报高置信问题（`aliases` 目前恒为空、
`time_marker` 常缺失，规则据此保守设计）；ai 模式额外做一次人物弧光漂移的
LLM 复核，失败时静默降级为仅本地规则，主链路不中断。
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel

from .llm_client import LLMClient, loads_json_object
from .metrics import metrics
from .prompt_catalog import CONTINUITY_REVIEW_PROMPT
from .screenplay import Dialogue, GlobalStoryState, Screenplay
from .security import DATA_FENCE_NOTICE

CONTINUITY_PROMPT_MARKER = "请检查以下剧本的跨章节一致性"

Severity = Literal["high", "medium", "low"]

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_AI_SCENE_LIMIT = 12
_CHAPTER_TITLE_PATTERN = re.compile(
    r"^\s*(第[零一二两三四五六七八九十百千万\d]+[章节回]|chapter\s*\d+)", re.IGNORECASE
)


class ContinuityFinding(BaseModel):
    """一条一致性问题。scene_id 为空表示全局层面的问题。"""

    scene_id: str = ""
    kind: str
    severity: Severity = "medium"
    detail: str
    suggestion: str = ""


def _character_names(global_state: GlobalStoryState) -> dict[str, str]:
    return {character.id: character.name for character in global_state.characters}


def _check_character_chapters(
    screenplay: Screenplay, global_state: GlobalStoryState
) -> list[ContinuityFinding]:
    """场景引用的人物，其出场章节表里应包含该场景的来源章节。"""
    findings: list[ContinuityFinding] = []
    by_id = {character.id: character for character in global_state.characters}
    for scene in screenplay.scenes:
        for character_id in scene.characters:
            state = by_id.get(character_id)
            if state is None:
                continue
            if scene.source_chapter and scene.source_chapter not in state.appearance_chapters:
                findings.append(
                    ContinuityFinding(
                        scene_id=scene.id,
                        kind="character_absent",
                        severity="high",
                        detail=(
                            f"{state.name}（{character_id}）出现在 {scene.id}，"
                            f"但全局状态表记录其出场章节为"
                            f"{'、'.join(state.appearance_chapters)}，不含"
                            f"《{scene.source_chapter}》。"
                        ),
                        suggestion="确认该人物是否应在本章出场，或补全全局状态表的出场章节。",
                    )
                )
    return findings


def _check_speakers_present(screenplay: Screenplay) -> list[ContinuityFinding]:
    """有台词的人物必须在本场在场人物名单里。"""
    findings: list[ContinuityFinding] = []
    names = _character_names(screenplay.global_state)
    for scene in screenplay.scenes:
        present = set(scene.characters_present)
        spoken: list[str] = []
        for element in scene.elements:
            if isinstance(element, Dialogue) and element.character not in present:
                if element.character not in spoken:
                    spoken.append(element.character)
        for character_id in spoken:
            findings.append(
                ContinuityFinding(
                    scene_id=scene.id,
                    kind="speaker_absent",
                    severity="high",
                    detail=(
                        f"{names.get(character_id, character_id)} 在 {scene.id} 有台词，"
                        "但不在本场 characters_present 名单中。"
                    ),
                    suggestion="把该人物加入在场名单，或改为画外音/删除其台词。",
                )
            )
    return findings


def _is_chapter_placeholder(location: str, chapter_titles: list[str]) -> bool:
    """地点是否只是章节名占位符。

    本地转换器抽不到地点时会退化成章节标题（如"第二章"）。这属于转换阶段的
    数据质量，不是跨章矛盾——若不排除，示例小说会刷出一片噪音告警。
    """
    if _CHAPTER_TITLE_PATTERN.match(location):
        return True
    return any(
        location and (location in title or title in location) for title in chapter_titles
    )


def _check_locations(
    screenplay: Screenplay, global_state: GlobalStoryState
) -> list[ContinuityFinding]:
    """场景地点应能在全局地点表里找到（允许包含关系，容忍"酒店客房/酒店"这类差异）。"""
    findings: list[ContinuityFinding] = []
    known = [location.name for location in global_state.locations if location.name]
    if not known:
        return findings
    chapter_titles = [title for title in screenplay.source.chapter_titles if title]
    for scene in screenplay.scenes:
        location = (scene.location or "").strip()
        if not location:
            continue
        if any(location in name or name in location for name in known):
            continue
        if _is_chapter_placeholder(location, chapter_titles):
            continue
        findings.append(
            ContinuityFinding(
                scene_id=scene.id,
                kind="unknown_location",
                severity="medium",
                detail=f"{scene.id} 的地点“{location}”不在全局地点表（{'、'.join(known)}）中。",
                suggestion="统一地点名称，或把新地点补进全局状态表。",
            )
        )
    return findings


def _check_timeline_order(
    screenplay: Screenplay, global_state: GlobalStoryState
) -> list[ContinuityFinding]:
    """场景顺序不应与章节时间线逆序。"""
    findings: list[ContinuityFinding] = []
    chapter_order = {event.chapter: event.order for event in global_state.timeline}
    if not chapter_order:
        return findings
    previous_order = 0
    previous_chapter = ""
    for scene in screenplay.scenes:
        order = chapter_order.get(scene.source_chapter)
        if order is None:
            continue
        if order < previous_order:
            findings.append(
                ContinuityFinding(
                    scene_id=scene.id,
                    kind="timeline_disorder",
                    severity="medium",
                    detail=(
                        f"{scene.id} 来自《{scene.source_chapter}》（第 {order} 章），"
                        f"却排在《{previous_chapter}》（第 {previous_order} 章）之后。"
                    ),
                    suggestion="调整场景顺序，或确认这是有意的插叙并在 beat 中说明。",
                )
            )
        else:
            previous_order = order
            previous_chapter = scene.source_chapter
    return findings


def _build_ai_prompt(screenplay: Screenplay, global_state: GlobalStoryState) -> str:
    characters = [
        {"id": item.id, "name": item.name, "goal": item.goal, "arc": item.arc}
        for item in global_state.characters
    ]
    scenes = [
        {
            "id": scene.id,
            "source_chapter": scene.source_chapter,
            "summary": scene.summary,
            "goal": scene.goal,
            "subtext": scene.subtext,
            "characters": scene.characters,
        }
        for scene in screenplay.scenes[:_AI_SCENE_LIMIT]
    ]
    return (
        f"{CONTINUITY_PROMPT_MARKER}，只关注人物目标与弧光是否前后矛盾。\n"
        f"{DATA_FENCE_NOTICE}\n"
        "人物设定（数据）："
        f"{json.dumps(characters, ensure_ascii=False)}\n"
        "场景摘要（数据）："
        f"{json.dumps(scenes, ensure_ascii=False)}\n\n"
        "只返回 JSON 对象，形如 "
        '{"findings": [{"scene_id": "scene-1", "detail": "…", "suggestion": "…"}]}；'
        "没有发现矛盾时返回 {\"findings\": []}。不要臆造问题，宁可少报。"
    )


def _check_arc_drift_with_ai(
    screenplay: Screenplay, global_state: GlobalStoryState, client=None
) -> list[ContinuityFinding]:
    llm = LLMClient(client=client, usage_label="AI continuity check")
    try:
        content = llm.complete_json(
            _build_ai_prompt(screenplay, global_state),
            prompt_id=CONTINUITY_REVIEW_PROMPT,
        )
        data = loads_json_object(content)
    except ValueError:
        # 复核失败不影响本地规则的结论。
        return []
    if not isinstance(data, dict):
        return []
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list):
        return []

    known_ids = {scene.id for scene in screenplay.scenes}
    findings: list[ContinuityFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail", "")).strip()
        if not detail:
            continue
        scene_id = str(item.get("scene_id", "")).strip()
        findings.append(
            ContinuityFinding(
                scene_id=scene_id if scene_id in known_ids else "",
                kind="arc_drift",
                severity="low",
                detail=detail,
                suggestion=str(item.get("suggestion", "")).strip(),
            )
        )
    return findings


def check_continuity(
    screenplay: Screenplay,
    global_state: GlobalStoryState | None = None,
    mode: str = "demo",
    client=None,
) -> list[ContinuityFinding]:
    """校验剧本的跨章节一致性，按严重度排序返回问题列表。

    global_state 缺省时用剧本自带的 `global_state`。mode="ai" 时额外做一次
    人物弧光漂移的 LLM 复核。
    """
    started = time.perf_counter()
    state = global_state if global_state is not None else screenplay.global_state

    findings = [
        *_check_character_chapters(screenplay, state),
        *_check_speakers_present(screenplay),
        *_check_locations(screenplay, state),
        *_check_timeline_order(screenplay, state),
    ]
    if mode == "ai":
        findings.extend(_check_arc_drift_with_ai(screenplay, state, client=client))

    findings.sort(key=lambda item: (_SEVERITY_ORDER.get(item.severity, 9), item.scene_id))
    metrics.record_task(
        "continuity_check",
        mode=mode,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=True,
        extra={
            "findings": len(findings),
            "high": sum(1 for item in findings if item.severity == "high"),
            "scene_count": len(screenplay.scenes),
        },
    )
    return findings


def summarize_findings(findings: list[ContinuityFinding]) -> dict:
    """给 Agent / 前端用的紧凑摘要。"""
    by_kind: dict[str, int] = {}
    for item in findings:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    return {
        "total": len(findings),
        "high": sum(1 for item in findings if item.severity == "high"),
        "medium": sum(1 for item in findings if item.severity == "medium"),
        "low": sum(1 for item in findings if item.severity == "low"),
        "by_kind": dict(sorted(by_kind.items())),
    }
