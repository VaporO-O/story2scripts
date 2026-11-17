"""Authoring projections and edits preserve story identity and expose stale logic."""

from collections import Counter
from itertools import combinations

from .creative_models import EditingState, SceneGroup
from .logic_engine import reason_story
from .logic_models import OPERATION_LABELS
from .plot_state import screenplay_hash
from .screenplay import Screenplay


def apply_edit(base, *, scene=None, structure=None, creative_brief=None):
    candidate = base.model_copy(deep=True)
    if scene is not None:
        index = next((i for i, s in enumerate(base.scenes) if s.id == scene.id), None)
        if index is None:
            raise ValueError("编辑场景不存在。")
        old = base.scenes[index]
        for key in ("id", "source_chapter", "timeline_mode", "event_time_scene_id"):
            if getattr(old, key) != getattr(scene, key):
                raise ValueError("内联编辑不能改变场景 ID、出处和事件时间，请使用剧情改写。")
        candidate.scenes[index] = scene
        if scene != old:
            dirty = list(base.editing.dirty_scene_ids) if base.editing else []
            if scene.id not in dirty:
                dirty.append(scene.id)
            candidate.editing = EditingState(
                based_on_hash=base.editing.based_on_hash if base.editing else screenplay_hash(base),
                dirty_scene_ids=dirty,
                note="正文或场景描述已编辑；旧事件提取与审校结论需重新核对。",
            )
    if structure is not None:
        candidate.structure = structure
    if creative_brief is not None:
        candidate.creative_brief = creative_brief
    return Screenplay.model_validate(candidate.model_dump(mode="json"))


def projection(script, scene_id="", boundary="end"):
    scene_id = scene_id or script.scenes[-1].id
    positions = {s.id: i for i, s in enumerate(script.scenes)}
    if scene_id not in positions:
        raise ValueError("所选场景不存在。")
    cutoff = positions[scene_id] + (boundary == "end")
    prefix = script.scenes[:cutoff]
    ledger = script.plot_state.logic if script.plot_state else None
    dirty = bool(script.editing and script.editing.status == "needs_check")
    report = (
        reason_story(
            script,
            ledger,
            script.plot_state.changes,
            **{"stop_after" if boundary == "end" else "stop_before": scene_id},
        )
        if ledger
        else None
    )
    authoritative = bool(report and report.valid and not dirty)
    facts = report.final_facts if authoritative else []
    facts_by_character = {c.id: [] for c in script.characters}
    for f in facts:
        if f.subject in facts_by_character:
            facts_by_character[f.subject].append(f.model_dump(mode="json"))
    appearances = {
        c.id: [
            s.id
            for s in prefix
            if c.id
            in set(s.characters_present) | {e.character for e in s.elements if e.type == "dialogue"}
        ]
        for c in script.characters
    }
    producers = {}
    if ledger:
        for i, seed in enumerate(ledger.initial_facts):
            producers[f"initial:{i}"] = {
                "type": "assumption" if seed.assumption else "initial",
                "explanation": seed.explanation,
                "evidence": [e.model_dump(mode="json") for e in seed.evidence],
            }
        for s in ledger.scenes[:cutoff]:
            for event in s.events:
                producers[event.id] = {
                    "type": "event",
                    "scene_id": s.scene_id,
                    "evidence": [e.model_dump(mode="json") for e in event.evidence],
                }
    # Event-time relations come only from replay, not final novel profiles.
    relations, seen = [], set()
    properties = {p.id: p for p in ledger.custom_properties} if ledger else {}
    for f in facts:
        if f.target in appearances and f.subject in appearances:
            symmetric = f.predicate == "relationship" or (
                f.predicate in properties and properties[f.predicate].symmetric
            )
            key = (*sorted((f.subject, f.target)), f.predicate) if symmetric else f.key()
            if key not in seen:
                seen.add(key)
                source = next(
                    (step.event_id for step in reversed(report.steps) if f in step.effects), ""
                )
                if not source:
                    source = next(
                        (
                            f"initial:{i}"
                            for i, seed in enumerate(ledger.initial_facts)
                            if seed.fact == f
                            or (
                                symmetric
                                and seed.fact.subject == f.target
                                and seed.fact.target == f.subject
                                and seed.fact.predicate == f.predicate
                                and seed.fact.value == f.value
                            )
                        ),
                        "",
                    )
                relations.append(
                    {
                        "from": f.subject,
                        "to": f.target,
                        "label": f.value,
                        "predicate": f.predicate,
                        "source": source,
                        "kind": "fact",
                        "symmetric": symmetric,
                    }
                )
    cooccurrences = Counter()
    for scene in prefix:
        cooccurrences.update(combinations(sorted(set(scene.characters_present)), 2))
    for (a, b), count in cooccurrences.items():
        if not any({r["from"], r["to"]} == {a, b} for r in relations):
            relations.append(
                {
                    "from": a,
                    "to": b,
                    "label": f"同场 {count} 次",
                    "kind": "cooccurrence",
                    "source": "",
                }
            )
    events = []
    names = {r.id: r.name for r in ledger.custom_rules} if ledger else {}
    for step in report.steps if report else []:
        if step.event_id.startswith(("presence:", "speech:")):
            continue
        events.append(
            {
                **step.model_dump(mode="json"),
                "label": names.get(
                    step.rule.removeprefix("custom:"),
                    OPERATION_LABELS.get(
                        step.rule, {"act": "行动", "use_item": "使用物品"}.get(step.rule, step.rule)
                    ),
                ),
                "evidence": producers.get(step.event_id, {}).get("evidence", []),
            }
        )
    return {
        "hash": screenplay_hash(script),
        "scene_id": scene_id,
        "boundary": boundary,
        "authoritative": authoritative,
        "stale": dirty,
        "has_logic": bool(ledger),
        "note": "显示已确认事件在当前场景边界的状态；回忆不修改当下。"
        if authoritative
        else "当前仅可查看出场关系与待核验事件；同场不代表朋友或敌人。",
        "characters": [
            {
                "id": c.id,
                "name": c.name,
                "scene_ids": appearances[c.id],
                "facts": facts_by_character[c.id],
            }
            for c in script.characters
        ],
        "relationships": relations,
        "events": events,
        "sources": producers,
        "dependencies": report.dependencies if report else {},
        "issues": [i.model_dump(mode="json") for i in report.issues] if report else [],
        "scenes": [
            {
                "id": s.id,
                "summary": s.summary,
                "timeline_mode": s.timeline_mode,
                "event_time_scene_id": s.event_time_scene_id,
            }
            for s in prefix
        ],
    }


def balanced_groups(script, kind, count, target_minutes=None):
    if count > len(script.scenes):
        raise ValueError("幕/集数量不能超过场景数。")
    groups = []
    for i in range(count):
        start, end = len(script.scenes) * i // count, len(script.scenes) * (i + 1) // count
        groups.append(
            SceneGroup(
                id=f"{kind}-{i + 1}",
                kind=kind,
                title=f"第 {i + 1} {'幕' if kind == 'act' else '集'}",
                scene_ids=[s.id for s in script.scenes[start:end]],
                target_minutes=target_minutes,
            )
        )
    return groups


def annotated_emotions(script, scene_ids=()):
    selected = set(scene_ids)
    if selected - {s.id for s in script.scenes}:
        raise ValueError("情绪分析引用未知场景。")
    result = []
    for scene in script.scenes:
        if selected and scene.id not in selected:
            continue
        for i, e in enumerate(scene.elements):
            if e.type == "dialogue":
                result.append(
                    {
                        "scene_id": scene.id,
                        "element_index": i,
                        "character_id": e.character,
                        "quote": e.text,
                        "emotion": e.emotion.strip() or "未标注",
                        "rationale": e.parenthetical,
                        "source": "script" if e.emotion.strip() else "unknown",
                    }
                )
    return result
