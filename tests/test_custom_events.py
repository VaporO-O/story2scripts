"""Custom events run through fixed, typed rules without trusting model approval."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from story2script.custom_events import registry_errors
from story2script.logic_engine import event_for_change, fact, reason_story
from story2script.logic_models import CustomProperty, CustomRule, Event, InitialFact, RuleStep
from story2script.main import app
from story2script.plot_api import switch_version
from story2script.plot_models import PlotVersionRequest
from story2script.plot_revision import check_base, create_plan, generate_revision
from story2script.plot_state import PlotChange, screenplay_hash
from story2script.plot_store import load_record, save_record
from story2script.yaml_export import screenplay_to_yaml
from test_logic_engine import ENTITIES, event, opening, replay, world
from test_plot_revision import PlotModelStub, fixture_logic, plan_request, plot_script


def health_property():
    return CustomProperty(
        id="custom:health",
        name="健康状态",
        subject_kind="character",
        values=["健康", "受伤"],
        usable_values=["健康"],
    )


def injury_rule():
    return CustomRule(
        id="injury",
        name="受重伤",
        description="受伤后不能行动，直到康复事件发生。",
        requires=[fact("$actor", "custom:health", "健康")],
        effects=[fact("$actor", "custom:health", "受伤")],
    )


def recovery_rule():
    return CustomRule(
        id="recovery",
        name="康复",
        description="受伤且存活的角色康复。",
        active_roles=[],
        requires=[fact("$actor", "alive", "true"), fact("$actor", "custom:health", "受伤")],
        effects=[fact("$actor", "custom:health", "健康")],
    )


def custom_world():
    state = world(opening() + [fact(c, "custom:health", "健康") for c in ("a", "b", "c")])
    state.properties = {"custom:health": health_property()}
    state.rules = {r.id: r for r in [injury_rule(), recovery_rule()]}
    return state


def custom_event(rule="injury", actor="a", **kwargs):
    return Event(
        id=f"custom-{rule}-{actor}", kind="custom", actor=actor, custom_rule=rule, **kwargs
    )


def test_injury_blocks_actions_and_explicit_recovery_restores_them():
    state = custom_world()
    step, error = state.apply(custom_event(), "s0", approved=True)
    assert error is None and step.effects == [fact("a", "custom:health", "受伤")]
    blocked, error = state.apply(event("act"), "s1")
    assert error and any(c.established_by == "custom-injury-a" for c in blocked.checks)
    assert state.apply(custom_event("recovery"), "s2", approved=True)[1] is None
    assert state.apply(event("act"), "s3")[1] is None


def test_unknown_custom_state_never_enables_action():
    state = custom_world()
    state.values.pop(("a", "custom:health", ""))
    proof, error = state.apply(event("act"), "s0")
    assert error and proof.checks[-1].status == "unknown"


def test_user_can_lock_custom_outcome_until_explicit_change():
    state = custom_world()
    state.rules["injury"].lock_effects = True
    state.apply(custom_event(), "s0", approved=True)
    assert state.apply(custom_event("recovery"), "s1")[1] is not None
    assert state.apply(custom_event("recovery"), "s1", approved=True)[1] is None


@pytest.mark.parametrize("mutation", ["rule", "param", "actor", "wrong_type", "unused_slot"])
def test_unknown_rules_invalid_bindings_and_types_are_rejected(mutation):
    state = custom_world()
    rule = CustomRule(
        id="gift",
        name="赠送钥匙",
        description="把钥匙赠给收礼者。",
        parameters={"recipient": "character", "gift": "item"},
        requires=[fact("$gift", "owner", "$actor")],
        steps=[RuleStep(kind="transfer", actor="$actor", target="$recipient", item="$gift")],
    )
    state.rules[rule.id] = rule
    attempt = custom_event("gift", bindings={"recipient": "b", "gift": "key"})
    if mutation == "rule":
        attempt.custom_rule = "unknown"
    elif mutation == "param":
        attempt.bindings["extra"] = "c"
    elif mutation == "actor":
        attempt.actor = "key"
    elif mutation == "wrong_type":
        attempt.bindings["recipient"] = "key"
    else:
        attempt.item = "key"
    before = dict(state.values)
    step, error = state.apply(attempt, "s0")
    assert error and not step.effects and state.values == before


def test_composite_event_rolls_back_when_late_step_fails():
    state = custom_world()
    state.rules["gift_and_learn"] = CustomRule(
        id="gift_and_learn",
        name="赠物并传讯",
        description="交钥匙后从对方获取秘密。",
        parameters={"recipient": "character"},
        requires=[fact("key", "owner", "$actor")],
        steps=[
            RuleStep(kind="transfer", actor="$actor", target="$recipient", item="key"),
            RuleStep(kind="learn", actor="$actor", source_character="$recipient", secret="secret"),
        ],
    )
    before = dict(state.values)
    proof, error = state.apply(custom_event("gift_and_learn", bindings={"recipient": "b"}), "s0")
    assert error and proof.status == "blocked" and not proof.effects
    assert proof.substeps[0].status == "applied" and proof.substeps[1].status == "blocked"
    assert state.values == before


def test_composite_success_exports_parent_provenance():
    state = custom_world()
    state.rules["pass_letter"] = CustomRule(
        id="pass_letter",
        name="递信并阅读",
        description="B 接信并读取秘密。",
        parameters={"recipient": "character"},
        requires=[fact("letter", "owner", "$actor")],
        steps=[
            RuleStep(kind="transfer", actor="$actor", target="$recipient", item="letter"),
            RuleStep(kind="learn", actor="$recipient", source_item="letter", secret="secret"),
        ],
    )
    proof, error = state.apply(custom_event("pass_letter", bindings={"recipient": "b"}), "s0")
    assert error is None and len(proof.substeps) == 2
    assert (
        state.require(fact("b", "knows", "true", "secret")).established_by == "custom-pass_letter-a"
    )
    assert not any("/step:" in cause for cause in proof.causes)


def test_custom_event_cannot_smuggle_resurrection_or_rewrite_core_effects():
    with pytest.raises(ValidationError, match="内置步骤"):
        CustomRule(
            id="cheat",
            name="作弊",
            description="直接复活",
            requires=[fact("$actor", "alive", "false")],
            effects=[fact("$actor", "alive", "true")],
        )
    state = custom_world()
    state.apply(event("death"), "s0", approved=True)
    state.rules["revival"] = CustomRule(
        id="revival",
        name="复苏",
        description="明确复活事件。",
        active_roles=[],
        requires=[fact("$actor", "alive", "false")],
        steps=[RuleStep(kind="revive", actor="$actor")],
    )
    assert state.apply(custom_event("revival"), "s1")[1]
    assert state.apply(custom_event("revival"), "s1", approved=True)[1] is None


def test_destroyed_item_cannot_be_read_or_transferred():
    state = world(opening() + [fact(x, "custom:condition", "完好") for x in ("letter", "key")])
    state.properties = {
        "custom:condition": CustomProperty(
            id="custom:condition",
            name="物品状态",
            subject_kind="item",
            values=["完好", "损毁"],
            usable_values=["完好"],
        )
    }
    state.rules = {
        "destroy": CustomRule(
            id="destroy",
            name="销毁物品",
            description="持有人销毁手里的物品。",
            parameters={"object": "item"},
            requires=[fact("$object", "owner", "$actor")],
            effects=[fact("$object", "custom:condition", "损毁")],
        )
    }
    assert state.apply(custom_event("destroy", bindings={"object": "letter"}), "s0")[1] is None
    assert state.apply(event("learn", source_item="letter", secret="secret"), "s1")[1]
    assert state.apply(event("transfer", item="letter", target="b"), "s1")[1]


def test_symmetric_custom_relationship_and_conflicts():
    state = world()
    state.properties = {
        "custom:marriage": CustomProperty(
            id="custom:marriage",
            name="婚姻",
            subject_kind="character",
            target_kind="character",
            values=["未婚", "已婚"],
            symmetric=True,
        )
    }
    state.rules = {
        "marry": CustomRule(
            id="marry",
            name="结婚",
            description="两位角色结婚。",
            parameters={"partner": "character"},
            active_roles=["actor", "partner"],
            requires=[fact("$partner", "alive", "true")],
            effects=[fact("$actor", "custom:marriage", "已婚", "$partner")],
        )
    }
    proof, error = state.apply(custom_event("marry", bindings={"partner": "b"}), "s0")
    assert error is None and len(proof.effects) == 2
    assert state.require(fact("b", "custom:marriage", "已婚", "a")).status == "true"
    state.rules["marry"].effects.append(fact("$partner", "custom:marriage", "未婚", "$actor"))
    assert state.apply(custom_event("marry", bindings={"partner": "b"}), "s0")[1]


@pytest.mark.parametrize(
    "bad", ["undefined_property", "missing_parameter", "unknown_role", "bad_step", "bad_effect"]
)
def test_rule_definitions_are_checked_even_before_execution(bad):
    _, ledger, _ = replay([[]])
    ledger.custom_properties = [health_property()]
    rule = injury_rule()
    if bad == "undefined_property":
        rule.requires[0].predicate = "custom:unknown"
    elif bad == "missing_parameter":
        rule.effects[0].subject = "$unknown"
    elif bad == "unknown_role":
        rule.active_roles = ["patient"]
    elif bad == "bad_step":
        rule.steps = [RuleStep(kind="move", actor="$actor", destination="key")]
    else:
        rule.effects[0].value = "不在状态域"
    ledger.custom_rules = [rule]
    assert registry_errors(ledger, ENTITIES)


def test_custom_causal_effect_propagates_and_flashback_does_not_pollute_present():
    script, ledger, _ = replay([[event("act")], [custom_event()], [event("act")]])
    ledger.scenes[2].events[0].id = "later-action"
    ledger.scenes[2].coverage[0].event_ids = ["later-action"]
    ledger.custom_properties = [health_property()]
    ledger.custom_rules = [injury_rule()]
    ledger.initial_facts += [
        InitialFact(fact=fact(c, "custom:health", "健康"), assumption=True, explanation="开场状态")
        for c in ("a", "b", "c")
    ]
    report = reason_story(script, ledger)
    assert not report.valid and report.dependencies["s2"] == ["s1"]
    script.scenes[1].timeline_mode = "flashback"
    script.scenes[1].event_time_scene_id = "s0"
    report = reason_story(script, ledger)
    assert report.valid and fact("a", "custom:health", "健康") in report.final_facts


def injury_logic(script):
    ledger = fixture_logic(script)
    if not ledger.custom_rules:
        ledger.custom_properties = [health_property()]
        ledger.custom_rules = [injury_rule(), recovery_rule()]
        ledger.initial_facts += [
            InitialFact(
                fact=fact(c.id, "custom:health", "健康"),
                assumption=True,
                explanation="开场未见受伤，需作者确认",
            )
            for c in script.characters
        ]
    return ledger


class CustomModelStub(PlotModelStub):
    def complete_json(self, prompt, **kwargs):
        raw = super().complete_json(prompt, **kwargs)
        if kwargs["prompt_id"] == "plot.plan":
            ctx = json.loads(prompt.split("上下文 JSON：", 1)[1])
            from story2script.screenplay import Screenplay

            response = json.loads(raw)
            response["logic"] = injury_logic(
                Screenplay.model_validate(ctx["screenplay"])
            ).model_dump(mode="json")
            response["target_event"].update(
                custom_rule=ctx["requested_parameters"].get("custom_rule") or "injury", bindings={}
            )
            return json.dumps(response, ensure_ascii=False)
        return raw


def test_natural_custom_plan_generate_adopt_restore_and_later_recover():
    stub = CustomModelStub()
    plan = create_plan(
        plan_request(operation="custom", instruction="让林夏受重伤，后面无法行动。"), client=stub
    )
    assert plan.change.custom_rule == "injury" and plan.baseline_reasoning.valid
    assert not plan.intervention_reasoning.valid
    assert any(i.scene_id == "scene-4" for i in plan.intervention_reasoning.issues)
    result = generate_revision(plan.plan_id, str(uuid4()), client=stub)
    assert result.status == "candidate", result.issues
    assert result.reasoning.valid
    adopted = switch_version(
        result.revision_id, PlotVersionRequest(yaml_text=plan_request().yaml_text)
    )
    stub = CustomModelStub()
    later = create_plan(
        plan_request(
            base=adopted.screenplay,
            operation="custom",
            custom_rule="recovery",
            scene_id="scene-4",
            instruction="让林夏康复。",
        ),
        client=stub,
    )
    assert later.baseline_reasoning.valid
    recovered = generate_revision(later.plan_id, str(uuid4()), client=stub)
    assert recovered.status == "candidate", recovered.issues
    assert fact("character-1", "custom:health", "健康") in recovered.reasoning.final_facts


def test_editing_frozen_rules_cannot_change_history_or_bypass_adoption():
    stub = CustomModelStub()
    plan = create_plan(plan_request(operation="custom"), client=stub)
    result = generate_revision(plan.plan_id, str(uuid4()), client=stub)
    saved = load_record("revision", result.revision_id)
    saved["screenplay"]["plot_state"]["logic"]["custom_rules"][0]["effects"][0]["value"] = "健康"
    save_record("revision", result.revision_id, saved)
    with pytest.raises(ValueError, match="已确认"):
        switch_version(result.revision_id, PlotVersionRequest(yaml_text=plan_request().yaml_text))
    next_plan = create_plan(
        plan_request(
            base=result.screenplay, operation="custom", custom_rule="recovery", scene_id="scene-4"
        ),
        client=CustomModelStub(),
    )
    edited = next_plan.logic.model_copy(deep=True)
    edited.custom_rules[0].description = "重新解释历史"
    response = TestClient(app).post(
        f"/api/plot/plans/{next_plan.plan_id}/logic", json={"logic": edited.model_dump(mode="json")}
    )
    assert response.status_code == 422 and "不能删除或改写" in response.json()["detail"]


def test_bad_registry_can_be_repaired_and_unknown_initial_blocks_generation():
    plan = create_plan(plan_request(operation="custom"), client=CustomModelStub())
    logic = plan.logic.model_copy(deep=True)
    logic.initial_facts = [f for f in logic.initial_facts if f.fact.predicate != "custom:health"]
    response = TestClient(app).post(
        f"/api/plot/plans/{plan.plan_id}/logic", json={"logic": logic.model_dump(mode="json")}
    )
    assert response.status_code == 200 and not response.json()["baseline_reasoning"]["valid"]
    from story2script.plot_state import RevisionPlan

    with pytest.raises(ValueError, match="未知前提"):
        check_base(RevisionPlan.model_validate(response.json()), plot_script())


def test_old_hash_stays_stable_with_new_empty_defaults():
    import hashlib

    script = plot_script()
    from story2script.plot_state import PlotContinuity

    script.plot_state = PlotContinuity(
        revision_id="old",
        logic=fixture_logic(script),
        changes=[
            PlotChange(
                change_id="old",
                operation="death",
                character_id="character-1",
                effective_scene_id="scene-4",
                instruction="死亡",
            )
        ],
    )
    data = script.model_dump(mode="json")
    for key in ("creative_brief", "structure", "editing"):
        data.pop(key, None)
    for event_data in data["plot_state"]["changes"] + [
        e for s in data["plot_state"]["logic"]["scenes"] for e in s["events"]
    ]:
        event_data.pop("custom_rule")
        event_data.pop("bindings")
    data["plot_state"]["logic"].pop("custom_rules")
    data["plot_state"]["logic"].pop("custom_properties")
    old_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert screenplay_hash(script) == old_hash


def test_extra_parameters_are_preserved_and_match_exact_change():
    change = PlotChange(
        change_id="x",
        operation="custom",
        character_id="a",
        effective_scene_id="s0",
        instruction="赠物",
        custom_rule="gift",
        bindings={"recipient": "b"},
    )
    assert event_for_change(change).bindings == {"recipient": "b"}


def test_custom_rules_round_trip_in_yaml():
    from story2script.yaml_export import screenplay_from_yaml
    from story2script.plot_state import PlotContinuity

    script = plot_script()
    script.plot_state = PlotContinuity(revision_id="test", logic=injury_logic(script))
    assert screenplay_from_yaml(screenplay_to_yaml(script)) == script
