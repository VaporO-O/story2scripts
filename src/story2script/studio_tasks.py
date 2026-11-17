"""Reusable creative operations, with an optional durable budget supplied by jobs."""

from .creative_models import EditingState
from .llm_client import LLMClient
from .logic_engine import reason_story
from .logic_integration import SCENE_LOGIC_PROMPT
from .plot_models import SceneReview, RevisionAudit
from .plot_revision import CallBudget
from .plot_state import screenplay_hash
from .plot_store import save_record
from .studio import annotated_emotions, apply_edit, balanced_groups
from .studio_models import EmotionAnalysis, StructureProposal
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml


def _result(script, **extra):
    return {
        "screenplay": script,
        "yaml_text": screenplay_to_yaml(script),
        "hash": screenplay_hash(script),
        **extra,
    }


def propose_structure(request, client=None, *, budget=None):
    script = screenplay_from_yaml(request.yaml_text)
    if request.count > len(script.scenes):
        raise ValueError("幕/集数量不能超过场景数。")
    if request.mode == "balanced":
        groups = balanced_groups(script, request.kind, request.count, request.target_minutes)
    else:
        own = client is None and budget is None
        client = (
            client
            or (budget.client if budget else None)
            or LLMClient(usage_label="AI narrative structure")
        )
        try:
            proposal = (budget or CallBudget(client, 1, 180, {}, lambda cp: None)).call(
                "依据目标、冲突和转折建议分幕/分集。只分组和命名，不改写、不新增、不重排任何场景。"
                "每一场必须恰好覆盖一次；分组连续且保持原顺序，组数必须等于 count。",
                StructureProposal,
                {
                    "kind": request.kind,
                    "count": request.count,
                    "scenes": [
                        {"id": s.id, "summary": s.summary, "beat": s.beat, "conflict": s.conflict}
                        for s in script.scenes
                    ],
                },
                "studio.structure",
            )
            groups = proposal.groups
        finally:
            if own:
                client.client.close()
        if len(groups) != request.count or any(g.kind != request.kind for g in groups):
            raise ValueError("模型没有遵守分组类型或组数。")
        for i, group in enumerate(groups):
            group.id = f"{request.kind}-{i + 1}"
            group.target_minutes = request.target_minutes
    structure = [g for g in script.structure if g.kind != request.kind] + groups
    return apply_edit(script, structure=structure)


def emotion_analysis(request, client=None, *, budget=None):
    script = screenplay_from_yaml(request.yaml_text)
    notes = annotated_emotions(script, request.scene_ids)
    if request.mode == "annotated":
        return {"hash": screenplay_hash(script), "notes": notes, "mode": request.mode}
    if not notes:
        return {"hash": screenplay_hash(script), "notes": [], "mode": request.mode}
    if sum(len(n["quote"]) for n in notes) > 60000:
        raise ValueError("请缩小情绪分析范围至当前场景，所选对白过长。")
    own = client is None and budget is None
    client = (
        client or (budget.client if budget else None) or LLMClient(usage_label="AI emotional arc")
    )
    try:
        result = (budget or CallBudget(client, 1, 180, {}, lambda cp: None)).call(
            "逐条分析给定对白情绪。notes 必须恰好覆盖所有给定对白，不能更换说话人或改词。"
            "quote 必须完整逐字对应；emotion 使用简短中文，无法判断时填不确定；rationale 解释文本依据。"
            "这是情绪解释，不是新增剧情事实，不能声称知道人物未表达的秘密。",
            EmotionAnalysis,
            {"dialogue": notes},
            "studio.emotions",
        )
    finally:
        if own:
            client.client.close()
    expected = {(n["scene_id"], n["element_index"]): n for n in notes}
    if len(result.notes) != len(expected) or {
        (n.scene_id, n.element_index) for n in result.notes
    } != set(expected):
        raise ValueError("情绪分析遗漏或重复对白。")
    for n in result.notes:
        original = expected[(n.scene_id, n.element_index)]
        if n.quote != original["quote"] or n.character_id != original["character_id"]:
            raise ValueError("情绪分析引用或说话人不匹配。")
    return {
        "hash": screenplay_hash(script),
        "notes": [{**n.model_dump(), "source": "ai"} for n in result.notes],
        "mode": "ai",
    }


def recheck_script(request, client=None, *, budget=None):
    script = screenplay_from_yaml(request.yaml_text)
    logic = script.plot_state.logic if script.plot_state else None
    dirty = script.editing.dirty_scene_ids if script.editing else []
    if request.mode == "rules":
        report = reason_story(script, logic, script.plot_state.changes) if logic else None
        return _result(
            script,
            valid=bool(report and report.valid and not dirty),
            reasoning=report,
            message="正文改动需要 AI 重提事件并核验。"
            if dirty
            else "规则已重放。"
            if report
            else "尚未建立事件账本，请先通过剧情改写预览建立。",
        )
    own = client is None and budget is None
    client = (
        client or (budget.client if budget else None) or LLMClient(usage_label="AI draft recheck")
    )
    try:
        budget = budget or CallBudget(client, len(dirty) + 1, 600, {}, lambda cp: None)
        issues = []
        if logic:
            for i, scene in enumerate(script.scenes):
                if scene.id not in dirty:
                    continue
                review = budget.call(
                    "核验当前编辑后的正文，忠实重提事件。" + SCENE_LOGIC_PROMPT,
                    SceneReview,
                    {
                        "scene": scene.model_dump(mode="json"),
                        "logic_entities": [e.model_dump(mode="json") for e in logic.entities],
                        "initial_facts": [f.model_dump(mode="json") for f in logic.initial_facts],
                        "custom_rules": [r.model_dump(mode="json") for r in logic.custom_rules],
                        "custom_properties": [
                            p.model_dump(mode="json") for p in logic.custom_properties
                        ],
                        "constraints": [
                            c.model_dump(mode="json") for c in script.plot_state.changes
                        ],
                    },
                    "studio.scene_recheck",
                )
                if (
                    review.scene_id != scene.id
                    or not review.logic
                    or review.logic.scene_id != scene.id
                ):
                    raise ValueError("事件提取返回了错误场景。")
                logic.scenes[i] = review.logic
                issues.extend(review.issues)
        report = reason_story(script, logic, script.plot_state.changes) if logic else None
        audit = budget.call(
            "审校编辑后的完整剧本，检查正文与事件账本一致、角色归属、信息来源和因果。"
            "scanned_scene_ids 必须按顺序列出全部场景，无法成立的剧情报告 issues。",
            RevisionAudit,
            {"screenplay": script.model_dump(mode="json")},
            "studio.audit",
        )
        if audit.scanned_scene_ids != [s.id for s in script.scenes]:
            raise ValueError("全剧审校没有覆盖所有场景。")
        issues.extend(audit.issues)
        if any(i.scene_id and i.scene_id not in {s.id for s in script.scenes} for i in issues):
            raise ValueError("审校问题引用了不存在的场景。")
        valid = not issues and (report.valid if report else True)
        if valid:
            script.editing = EditingState(
                status="checked",
                based_on_hash=screenplay_hash(script),
                note="事件与语义重检通过，仍需作者审阅。"
                if logic
                else "语义审校通过，尚无事件账本。",
            )
            save_record(
                "studio_checked", screenplay_hash(script), {"hash": screenplay_hash(script)}
            )
        else:
            script.editing = EditingState(
                based_on_hash=script.editing.based_on_hash
                if script.editing
                else screenplay_hash(script),
                dirty_scene_ids=list(dict.fromkeys(dirty + [s.id for s in script.scenes])),
                note="重提事件或全剧审校存在未解决问题，仍需修正。",
            )
        return _result(
            script,
            valid=valid,
            reasoning=report,
            issues=issues,
            message="重新核验通过。" if valid else "存在未解决问题，请修正或使用剧情联动改写。",
        )
    finally:
        if own:
            client.client.close()
