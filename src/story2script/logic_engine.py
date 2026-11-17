"""Deterministic forward chaining, event replay, and provenance for typed stories.

Unknown is never true. Each event commits atomically only after all premises
hold. Natural-language extraction is deliberately outside this module.
"""

from __future__ import annotations

from .logic_models import (
    Event,
    Fact,
    LogicIssue,
    ProofStep,
    ReasoningReport,
    RequirementCheck,
    StoryLogic,
)


def fact(subject, predicate, value, target=""):
    return Fact(subject=subject, predicate=predicate, value=value, target=target)


def validate_fact(value, entities, properties=None):
    def typed(entity_id, kind):
        if entities.get(entity_id) != kind:
            raise ValueError(f"实体 {entity_id} 应为 {kind}。")

    p = value.predicate
    if p.startswith("custom:"):
        prop = (properties or {}).get(p)
        if prop is None:
            raise ValueError(f"未定义的自定义状态：{p}")
        typed(value.subject, prop.subject_kind)
        if value.value not in prop.values:
            raise ValueError(f"{p} 的值必须属于 {prop.values}。")
        if prop.target_kind:
            typed(value.target, prop.target_kind)
        elif value.target:
            raise ValueError(f"{p} 不接受 target。")
        return
    typed(value.subject, "item" if p in ("owner", "contains") else "character")
    if p in ("alive", "available", "knows", "contains") and value.value not in ("true", "false"):
        raise ValueError(f"{p} 只接受 true/false。")
    if p == "at":
        typed(value.value, "location")
    elif p == "owner":
        typed(value.value, "character")
    elif p in ("knows", "contains"):
        typed(value.target, "secret")
    elif p == "relationship":
        typed(value.target, "character")
        if value.target == value.subject or value.value not in ("ally", "enemy", "neutral"):
            raise ValueError("关系必须指向另一角色，且为 ally/enemy/neutral。")
    if p not in ("knows", "contains", "relationship") and value.target:
        raise ValueError(f"{p} 不接受 target。")


def event_matches_change(event, change):
    return (
        event.kind == change.operation
        and event.actor == change.character_id
        and event.custom_rule == getattr(change, "custom_rule", "")
        and event.bindings == getattr(change, "bindings", {})
        and all(f in event.requires for f in getattr(change, "requires", []))
        and all(
            getattr(event, field) == getattr(change, field, "")
            for field in (
                "target",
                "item",
                "destination",
                "secret",
                "relationship",
                "source_character",
                "source_item",
            )
        )
    )


def event_for_change(change):
    return Event(
        id="intervention:" + change.change_id,
        kind=change.operation,
        actor=change.character_id,
        custom_rule=getattr(change, "custom_rule", ""),
        bindings=getattr(change, "bindings", {}),
        requires=getattr(change, "requires", []),
        **{
            field: getattr(change, field, "")
            for field in (
                "target",
                "item",
                "destination",
                "secret",
                "relationship",
                "source_character",
                "source_item",
            )
        },
    )


def effect_facts(event, rules=None, properties=None):
    if event.kind == "custom":
        from .custom_events import declared_effects

        return declared_effects(event, rules or {}, properties or {})
    a = event.actor
    if event.kind == "death":
        return [fact(a, "alive", "false"), fact(a, "available", "false")]
    if event.kind == "exit":
        return [fact(a, "available", "false")]
    if event.kind == "revive":
        return [fact(a, "alive", "true"), fact(a, "available", "true")]
    if event.kind == "return":
        return [fact(a, "available", "true")]
    if event.kind == "transfer":
        return [fact(event.item, "owner", event.target)]
    if event.kind == "move":
        return [fact(a, "at", event.destination)]
    if event.kind == "learn":
        return [fact(a, "knows", "true", event.secret)]
    if event.kind == "relationship":
        return [
            fact(a, "relationship", event.relationship, event.target),
            fact(event.target, "relationship", event.relationship, a),
        ]
    return []


class World:
    def __init__(self, entities, values=None, locks=None, properties=None, rules=None):
        self.entities = entities
        self.values = dict(values or {})  # key -> (Fact, producing event id, scene id)
        self.locks = dict(locks or {})
        self.properties = properties or {}
        self.rules = rules or {}

    def clone(self):
        return World(self.entities, self.values, self.locks, self.properties, self.rules)

    def validate(self, value):
        validate_fact(value, self.entities, self.properties)

    def check_lock(self, value, approved):
        if value.key() in self.locks and self.locks[value.key()] != value.value and not approved:
            if value.predicate in ("alive", "available"):
                raise ValueError("事件试图撤销用户的死亡/离场约束，需要显式复活/回归指令。")
            raise ValueError("事件试图改变已锁定状态，需要用户显式批准新事件。")

    def usable_checks(self, entity, checks):
        for prop in self.properties.values():
            if prop.usable_values and prop.subject_kind == self.entities.get(entity):
                check = self.require(fact(entity, prop.id, prop.usable_values[0]))
                check.allowed_values = list(prop.usable_values)
                check.status = (
                    "unknown"
                    if check.actual is None
                    else "true"
                    if check.actual in prop.usable_values
                    else "false"
                )
                checks.append(check)

    def active_checks(self, actor, checks):
        for p in ("alive", "available"):
            value = fact(actor, p, "true")
            self.validate(value)
            checks.append(self.require(value))
        self.usable_checks(actor, checks)

    def require(self, expected):
        stored = self.values.get(expected.key())
        actual = stored[0].value if stored else None
        return RequirementCheck(
            expected=expected,
            actual=actual,
            status="unknown" if actual is None else "true" if actual == expected.value else "false",
            established_by=stored[1] if stored else "",
        )

    def apply(self, event, scene_id, *, approved=False):
        if event.kind == "custom":
            from .custom_events import apply_custom

            return apply_custom(self, event, scene_id, approved)
        checks = []

        def need(value):
            self.validate(value)
            checks.append(self.require(value))

        def active(actor):
            self.active_checks(actor, checks)

        def together(first, second):
            location = self.values.get((first, "at", ""))
            if not location:
                # Use a separate diagnostic rather than inventing a location to compare.
                checks.append(
                    RequirementCheck(
                        expected=fact(first, "at", "<known location>"), status="unknown"
                    )
                )
            else:
                need(location[0])
                need(fact(second, "at", location[0].value))

        try:
            if event.custom_rule or event.bindings:
                raise ValueError("内置事件不能携带自定义规则或绑定参数。")
            if self.entities.get(event.actor) != "character":
                raise ValueError(f"未知角色：{event.actor}")
            mandatory = {
                "transfer": ("target", "item"),
                "move": ("destination",),
                "learn": ("secret",),
                "relationship": ("target", "relationship"),
                "use_item": ("item",),
            }.get(event.kind, ())
            allowed = set(mandatory) | (
                {"source_character", "source_item"} if event.kind == "learn" else set()
            )
            for name in (
                "target",
                "item",
                "destination",
                "secret",
                "relationship",
                "source_character",
                "source_item",
            ):
                if (name in mandatory and not getattr(event, name)) or (
                    name not in allowed and getattr(event, name)
                ):
                    raise ValueError(f"{event.kind} 的参数 {name} 缺失或不适用。")
            if event.kind == "revive":
                need(fact(event.actor, "alive", "false"))
            elif event.kind == "return":
                need(fact(event.actor, "alive", "true"))
                need(fact(event.actor, "available", "false"))
            else:
                active(event.actor)
            if event.kind == "transfer":
                if event.actor == event.target:
                    raise ValueError("物品不能转交给同一个角色。")
                active(event.target)
                need(fact(event.item, "owner", event.actor))
                self.usable_checks(event.item, checks)
                together(event.actor, event.target)
            elif event.kind == "use_item":
                need(fact(event.item, "owner", event.actor))
                self.usable_checks(event.item, checks)
            elif event.kind == "move":
                prior = self.values.get((event.actor, "at", ""))
                if not prior:
                    checks.append(
                        RequirementCheck(
                            expected=fact(event.actor, "at", "<known location>"), status="unknown"
                        )
                    )
                else:
                    need(prior[0])
            elif event.kind == "relationship":
                active(event.target)
            elif event.kind == "learn":
                if bool(event.source_character) == bool(event.source_item):
                    raise ValueError("获知秘密必须指定唯一的信息来源角色或物品。")
                if event.source_character:
                    if event.source_character == event.actor:
                        raise ValueError("角色不能以自己为来源凭空获知秘密。")
                    active(event.source_character)
                    need(fact(event.source_character, "knows", "true", event.secret))
                    together(event.source_character, event.actor)
                else:
                    need(fact(event.source_item, "owner", event.actor))
                    need(fact(event.source_item, "contains", "true", event.secret))
                    self.usable_checks(event.source_item, checks)
            for value in event.requires:
                need(value)
            effects = effect_facts(event)
            for value in effects:
                self.validate(value)
                self.check_lock(value, approved)
        except ValueError as exc:
            return ProofStep(
                scene_id=scene_id,
                event_id=event.id,
                rule=event.kind,
                status="blocked",
                checks=checks,
                effects=[],
                causes=[],
            ), LogicIssue(
                scene_id=scene_id, event_id=event.id, code="invalid_event", message=str(exc)
            )
        causes = list(dict.fromkeys(c.established_by for c in checks if c.established_by))
        failed = [c for c in checks if c.status != "true"]
        step = ProofStep(
            scene_id=scene_id,
            event_id=event.id,
            rule=event.kind,
            status="blocked" if failed else "applied",
            checks=checks,
            effects=[] if failed else effects,
            causes=causes,
        )
        if failed:
            detail = "；".join(
                f"{c.expected.subject}.{c.expected.predicate}"
                f"({c.expected.target}) 需要 {'/'.join(c.allowed_values) if c.allowed_values else c.expected.value}，"
                f"实际为 {c.actual if c.actual is not None else '未知'}"
                for c in failed
            )
            return step, LogicIssue(
                scene_id=scene_id,
                event_id=event.id,
                code="unmet_precondition",
                message=detail,
                checks=failed,
            )
        for value in effects:
            self.values[value.key()] = (value, event.id, scene_id)
            if approved and value.key() in self.locks:
                del self.locks[value.key()]
            if approved and event.kind in ("death", "exit"):
                self.locks[value.key()] = value.value
        return step, None


def _grounding_issue(evidence, scenes):
    scene = scenes.get(evidence.scene_id)
    if scene is None or evidence.element_index >= len(scene.elements):
        return "依据引用了不存在的场景或正文元素。"
    if evidence.quote not in scene.elements[evidence.element_index].text:
        return "依据不是对应正文元素的逐字片段。"
    return ""


def reason_story(
    screenplay,
    story: StoryLogic,
    changes=(),
    *,
    stop_after=None,
    stop_before=None,
    intervention=None,
):
    issues, steps, scanned = [], [], []
    entities = {e.id: e.kind for e in story.entities}
    scenes = {s.id: s for s in screenplay.scenes}
    positions = {s.id: i for i, s in enumerate(screenplay.scenes)}
    assumptions = [f for f in story.initial_facts if f.assumption]

    def issue(code, message, scene_id="", event_id=""):
        issues.append(LogicIssue(code=code, message=message, scene_id=scene_id, event_id=event_id))

    if len(entities) != len(story.entities):
        issue("entity", "逻辑实体 ID 重复。")
    if {e.id for e in story.entities if e.kind == "character"} != {
        c.id for c in screenplay.characters
    }:
        issue("entity", "逻辑人物表必须与剧本人名 ID 完全一致。")
    if [s.scene_id for s in story.scenes] != [s.id for s in screenplay.scenes]:
        issue("coverage", "逻辑场景必须按顺序覆盖完整剧本。")
    if any(sid and sid not in positions for sid in (stop_after, stop_before)):
        issue("timeline", "检查终点不存在。")
    for change in list(changes) + ([intervention] if intervention else []):
        if change.effective_scene_id not in positions:
            issue("goal", "变更生效场景不存在。")
        elif scenes[change.effective_scene_id].timeline_mode != "present":
            issue("goal", "用户变更必须发生在当下时间线。", change.effective_scene_id)
    from .custom_events import registry_errors

    properties = {p.id: p for p in story.custom_properties}
    rules = {r.id: r for r in story.custom_rules}
    for error in registry_errors(story, entities):
        issue("custom_definition", error)
    world = World(entities, properties=properties, rules=rules)
    for index, seed in enumerate(story.initial_facts):
        try:
            world.validate(seed.fact)
            if not seed.evidence and not seed.assumption:
                raise ValueError("无文字依据的初始事实必须明确标记为假设。")
            for evidence in seed.evidence:
                if story.initial_source:
                    texts = story.initial_source.get(evidence.scene_id, [])
                    error = (
                        "初始事实的依据不在保存的原文中。"
                        if evidence.element_index >= len(texts)
                        or evidence.quote not in texts[evidence.element_index]
                        else ""
                    )
                else:
                    error = _grounding_issue(evidence, scenes)
                if error:
                    raise ValueError(error)
            previous = world.values.get(seed.fact.key())
            if previous and previous[0].value != seed.fact.value:
                raise ValueError("同一个初始状态存在互相矛盾的值。")
            world.values[seed.fact.key()] = (seed.fact, f"initial:{index}", "")
        except ValueError as exc:
            issue("initial_fact", str(exc))
    for entity_id, kind in entities.items():
        if kind == "character":
            dead = world.values.get((entity_id, "alive", ""))
            available = world.values.get((entity_id, "available", ""))
            if dead and available and dead[0].value == "false" and available[0].value == "true":
                issue("initial_fact", f"{entity_id} 的初始状态不能同时死亡且可行动。")
    for value, _, _ in list(world.values.values()):
        if value.predicate == "relationship" or (
            properties.get(value.predicate) and properties[value.predicate].symmetric
        ):
            reverse = world.values.get((value.target, value.predicate, value.subject))
            if reverse and reverse[0].value != value.value:
                issue("initial_fact", "初始双向关系互相矛盾。")
            elif not reverse:
                original = world.values[value.key()]
                inverse = fact(value.target, value.predicate, value.value, value.subject)
                world.values[inverse.key()] = (inverse, original[1], "")
    if issues:
        return ReasoningReport(
            valid=False,
            issues=issues,
            steps=[],
            final_facts=[],
            dependencies={},
            scanned_scene_ids=[],
            assumptions=assumptions,
        )
    start_worlds = {}
    event_ids = set()
    producer_scenes = {}
    dependencies = {}
    for scene_logic in story.scenes:
        scene = scenes[scene_logic.scene_id]
        if stop_before and positions[scene.id] >= positions[stop_before]:
            break
        if stop_after and positions[scene.id] > positions[stop_after]:
            break
        start_worlds[scene.id] = world.clone()
        local = world
        if scene.timeline_mode != "present":
            anchor = scene.event_time_scene_id
            if anchor not in start_worlds or positions[anchor] >= positions[scene.id]:
                issue("timeline", "历史场景需要锚定到更早场景开始时。", scene.id)
                continue
            local = start_worlds[anchor].clone()
        scanned.append(scene.id)
        before_issues = len(issues)
        coverage = {c.element_index: c for c in scene_logic.coverage}
        if len(coverage) != len(scene_logic.coverage) or set(coverage) != set(
            range(len(scene.elements))
        ):
            issue("coverage", "每个动作/对白元素必须有唯一的事件覆盖或背景说明。", scene.id)
        local_ids = {e.id for e in scene_logic.events}
        if len(local_ids) != len(scene_logic.events) or event_ids.intersection(local_ids):
            issue("event_id", "事件 ID 不得重复。", scene.id)
        event_ids.update(local_ids)
        previous_index = -1
        for event in scene_logic.events:
            if (
                event.id.startswith(("speech:", "presence:", "intervention:", "initial:"))
                or "/step:" in event.id
            ):
                issue("event_id", "事件 ID 使用了引擎保留前缀。", scene.id, event.id)
            if not event.evidence or any(e.scene_id != scene.id for e in event.evidence):
                issue("evidence", "事件必须引用当前场景正文。", scene.id, event.id)
            for evidence in event.evidence:
                error = _grounding_issue(evidence, scenes)
                if error:
                    issue("evidence", error, scene.id, event.id)
                covered = coverage.get(evidence.element_index)
                if not covered or event.id not in covered.event_ids:
                    issue("coverage", "事件依据没有登记到对应正文覆盖项。", scene.id, event.id)
            event_index = min((e.element_index for e in event.evidence), default=-1)
            if event_index < previous_index:
                issue("timeline", "事件顺序与正文元素顺序矛盾。", scene.id, event.id)
            previous_index = event_index
        for c in scene_logic.coverage:
            if c.context_only == bool(c.event_ids) or set(c.event_ids) - local_ids:
                issue("coverage", "背景与事件覆盖标记矛盾或引用未知事件。", scene.id)
            for eid in c.event_ids:
                event = next((e for e in scene_logic.events if e.id == eid), None)
                if event and not any(e.element_index == c.element_index for e in event.evidence):
                    issue("coverage", "覆盖项指向的事件未引用该正文元素。", scene.id, eid)
        if len(issues) != before_issues:
            continue  # Never execute a structurally invalid scene.
        events = list(scene_logic.events)
        due = [c for c in changes if c.effective_scene_id == scene.id]
        if intervention and intervention.effective_scene_id == scene.id:
            events.append(event_for_change(intervention))
            due.append(intervention)
        matched = {}
        for change in due:
            matches = [i for i, event in enumerate(events) if event_matches_change(event, change)]
            if len(matches) != 1:
                issue("goal", "用户要求的事件必须恰好发生一次。", scene.id)
            else:
                matched[matches[0]] = change
                at = min(
                    (e.element_index for e in events[matches[0]].evidence),
                    default=len(scene.elements),
                )
                if any(e.type == "dialogue" for e in scene.elements[at + 1 :]):
                    issue("goal", "场末变更后还有对白，请把变更移至场景末尾。", scene.id)
        if due and list(matched) != list(range(len(events) - len(due), len(events))):
            issue("goal", "用户要求的变更必须按指令顺序发生在场景结尾。", scene.id)
        # All dialogue speakers act at their utterance, even if the extractor calls it context.
        dialogue_at = {}
        for i, element in enumerate(scene.elements):
            if element.type == "dialogue":
                dialogue_at[i] = Event(
                    id=f"speech:{scene.id}:{i}", kind="act", actor=element.character
                )
        consumed = set()
        acting = {e.actor for e in events if e.kind not in ("revive", "return")} | {
            e.character for e in scene.elements if e.type == "dialogue"
        }
        incoming = {e.actor for e in events if e.kind in ("revive", "return")}
        # Passive participants (a patient or prisoner) need not be able to act.
        # Their frozen template declares active_roles and explicit prerequisites.
        acting.update(value for e in events if e.kind == "custom" for value in e.bindings.values())
        presence = [
            Event(id=f"presence:{scene.id}:{cid}", kind="act", actor=cid)
            for cid in scene.characters_present
            if cid not in acting | incoming
        ]

        def execute(event, approved=False):
            step, error = local.apply(event, scene.id, approved=approved)
            steps.append(step)
            if error:
                issues.append(error)
            else:
                producer_scenes[event.id] = scene.id
            for cause in step.causes:
                dependency = producer_scenes.get(cause)
                if dependency and positions[dependency] < positions[scene.id]:
                    dependencies.setdefault(scene.id, set()).add(dependency)

        for event in presence:
            execute(event)
        for i, event in enumerate(events):
            order = min((e.element_index for e in event.evidence), default=len(scene.elements))
            for idx, speech in dialogue_at.items():
                if idx <= order and idx not in consumed:
                    execute(speech)
                    consumed.add(idx)
            execute(event, i in matched)
        for idx, speech in dialogue_at.items():
            if idx not in consumed:
                execute(speech)
        if scene.timeline_mode != "present":
            # Historical playback is checked on a snapshot and never changes the present.
            continue
    return ReasoningReport(
        valid=not issues,
        issues=issues,
        steps=steps,
        final_facts=[value[0] for value in world.values.values()],
        dependencies={k: sorted(v, key=positions.get) for k, v in dependencies.items()},
        scanned_scene_ids=scanned,
        assumptions=assumptions,
    )
