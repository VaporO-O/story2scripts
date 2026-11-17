"""Declarative, typed event templates. Strings are bindings, never executable code."""

import re

from .logic_models import Event, Fact, LogicIssue, ProofStep


def bind_value(value, bindings):
    if not value.startswith("$"):
        return value
    name = value[1:]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name not in bindings:
        raise ValueError(f"未知或非法的事件参数引用：{value}")
    return bindings[name]


def bind_fact(pattern, bindings):
    return Fact(
        subject=bind_value(pattern.subject, bindings),
        predicate=pattern.predicate,
        value=bind_value(pattern.value, bindings),
        target=bind_value(pattern.target, bindings),
    )


def bind_step(pattern, bindings, event_id):
    values = pattern.model_dump()
    for name, value in list(values.items()):
        if isinstance(value, str) and name != "kind":
            values[name] = bind_value(value, bindings)
    values["requires"] = [bind_fact(f, bindings) for f in pattern.requires]
    return Event(id=event_id, **values)


def event_bindings(event, rule, entities):
    if set(event.bindings) != set(rule.parameters):
        raise ValueError("自定义事件的参数必须与已确认规则完全一致。")
    bound = {"actor": event.actor, **event.bindings}
    for name, kind in {"actor": "character", **rule.parameters}.items():
        if entities.get(bound[name]) != kind:
            raise ValueError(f"参数 {name} 必须绑定已有的 {kind} 实体。")
    return bound


def symmetric_effects(values, properties):
    result = {}
    for value in values:
        prop = properties.get(value.predicate)
        variants = [value]
        if prop and prop.symmetric:
            variants.append(
                Fact(
                    subject=value.target,
                    predicate=value.predicate,
                    target=value.subject,
                    value=value.value,
                )
            )
        for item in variants:
            if item.key() in result and result[item.key()].value != item.value:
                raise ValueError("自定义事件对同一状态给出了矛盾的效果。")
            result[item.key()] = item
    return list(result.values())


def registry_errors(story, entities):
    """Type-check even unused rules with symbolic entities before replay starts."""
    from .logic_engine import World, validate_fact

    properties = {p.id: p for p in story.custom_properties}
    rules = {r.id: r for r in story.custom_rules}
    errors = []
    if len(properties) != len(story.custom_properties) or len(rules) != len(story.custom_rules):
        errors.append("自定义状态或事件规则 ID 重复。")
    for rule in story.custom_rules:
        try:
            if (
                "actor" in rule.parameters
                or len(rule.parameters) > 16
                or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", p) for p in rule.parameters)
            ):
                raise ValueError("参数名必须为小写标识符；actor 为保留角色，参数数目不能超过 16。")
            roles = {"actor": "character", **rule.parameters}
            if any(roles.get(p) != "character" for p in rule.active_roles):
                raise ValueError("可行动参与者必须引用已有角色参数。")
            bound = {key: f"__parameter_{rule.id}_{key}" for key in roles}
            symbolic = {**entities, **{bound[key]: kind for key, kind in roles.items()}}
            for pattern in rule.requires + rule.effects:
                validate_fact(bind_fact(pattern, bound), symbolic, properties)
            symmetric_effects([bind_fact(p, bound) for p in rule.effects], properties)
            for i, pattern in enumerate(rule.steps):
                step = bind_step(pattern, bound, f"check-{i}")
                _, error = World(symbolic, properties=properties).apply(step, "")
                if error and error.code == "invalid_event":
                    raise ValueError(error.message)
        except ValueError as exc:
            errors.append(f"规则 {rule.name}：{exc}")
    return errors


def declared_effects(event, rules, properties):
    """Projection for plan display/legacy lifecycle checks; execution uses apply_custom."""
    from .logic_engine import effect_facts

    rule = rules.get(event.custom_rule)
    if rule is None:
        return []
    bindings = {"actor": event.actor, **event.bindings}
    effects = []
    for i, step in enumerate(rule.steps):
        effects.extend(effect_facts(bind_step(step, bindings, f"declared-{i}")))
    effects.extend(symmetric_effects([bind_fact(p, bindings) for p in rule.effects], properties))
    return effects


def apply_custom(world, event, scene_id, approved=False):
    checks, substeps, effects = [], [], []
    internal_ids = set()
    rule = world.rules.get(event.custom_rule)
    label = f"custom:{event.custom_rule}"

    def finish(message="", code="custom_event"):
        causes = list(
            dict.fromkeys(
                c.established_by
                for c in checks
                if c.established_by and c.established_by not in internal_ids
            )
        )
        proof = ProofStep(
            scene_id=scene_id,
            event_id=event.id,
            rule=label,
            status="blocked" if message else "applied",
            checks=checks,
            effects=[] if message else effects,
            causes=causes,
            substeps=substeps,
        )
        issue = (
            LogicIssue(
                scene_id=scene_id,
                event_id=event.id,
                code=code,
                message=message,
                checks=[c for c in checks if c.status != "true"],
            )
            if message
            else None
        )
        return proof, issue

    try:
        if rule is None:
            raise ValueError("自定义事件引用了未注册的规则。")
        if any(
            getattr(event, key)
            for key in (
                "target",
                "item",
                "destination",
                "secret",
                "relationship",
                "source_character",
                "source_item",
            )
        ):
            raise ValueError("自定义事件使用 bindings 绑定参与者，不混用内置事件参数。")
        bindings = event_bindings(event, rule, world.entities)
        for role in rule.active_roles:
            world.active_checks(bindings[role], checks)
        for value in [bind_fact(p, bindings) for p in rule.requires] + event.requires:
            world.validate(value)
            checks.append(world.require(value))
        failed = [c for c in checks if c.status != "true"]
        if failed:
            return finish("自定义事件前提不成立或未知。", "unmet_precondition")
        direct = symmetric_effects([bind_fact(p, bindings) for p in rule.effects], world.properties)
        for value in direct:
            if not value.predicate.startswith("custom:"):
                raise ValueError("内置状态只能通过内置事件步骤修改。")
            world.validate(value)
            world.check_lock(value, approved)
        # Work only on a clone. A late failing primitive cannot leak earlier effects.
        staged = world.clone()
        writes = {}
        for i, pattern in enumerate(rule.steps):
            step_event = bind_step(pattern, bindings, f"{event.id}/step:{i}")
            internal_ids.add(step_event.id)
            proof, error = staged.apply(step_event, scene_id, approved=approved)
            substeps.append(proof)
            checks.extend(proof.checks)
            if error:
                return finish(
                    f"步骤 {i + 1}（{pattern.kind}）失败，整个事件未提交：{error.message}",
                    error.code,
                )
            writes.update({f.key(): f for f in proof.effects})
        writes.update({f.key(): f for f in direct})
        effects = list(writes.values())
        for value in effects:
            staged.values[value.key()] = (value, event.id, scene_id)
            if value.predicate.startswith("custom:") and approved:
                staged.locks.pop(value.key(), None)
            if approved and rule.lock_effects:
                staged.locks[value.key()] = value.value
        world.values = staged.values
        world.locks = staged.locks
        return finish()
    except (ValueError, KeyError) as exc:
        return finish(str(exc), "invalid_event")
