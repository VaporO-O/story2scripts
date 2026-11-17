import copy
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from story2script.main import app
from story2script.plot_api import switch_version
from story2script.plot_models import PlotPlanRequest, PlotVersionRequest
from story2script.plot_revision import check_base, create_plan, generate_revision, hard_issues
from story2script.plot_state import PlotChange, PlotContinuity, screenplay_hash
from story2script.logic_engine import event_for_change, fact
from story2script.logic_models import (
    Event,
    Entity,
    InitialFact,
    Evidence,
    SceneLogic,
    ElementCoverage,
    StoryLogic,
)
from story2script.plot_store import load_record, save_record
from story2script.screenplay import Screenplay, Scene
from story2script.yaml_export import screenplay_from_yaml, screenplay_to_yaml
from test_screenplay_schema import sample_screenplay


def plot_script():
    data = sample_screenplay()
    data["characters"].append(
        {
            "id": "character-2",
            "name": "周宁",
            "description": "调查员",
            "motivation": "寻找真相",
            "arc": "承担责任",
        }
    )
    data["global_state"]["characters"][0]["aliases"] = ["小夏"]
    original = data["scenes"][0]
    scenes = []
    for index, (text, people, props) in enumerate(
        [
            ("林夏在车站阅读随身的信。", ["character-1"], ["信"]),
            ("林夏把钥匙交给周宁，约好明天调查。", ["character-1", "character-2"], ["钥匙"]),
            ("周宁凭约定送来的钥匙打开仓库。", ["character-2"], ["钥匙", "仓库"]),
            ("林夏查看仓库里的痕迹。", ["character-1"], ["证据"]),
        ],
        1,
    ):
        scene = copy.deepcopy(original)
        scene.update(
            id=f"scene-{index}",
            summary=text,
            characters=people,
            characters_present=people,
            props=props,
            elements=[{"type": "action", "text": text}],
        )
        scenes.append(scene)
    data["scenes"] = scenes
    return Screenplay.model_validate(data)


def plan_request(base=None, **kwargs):
    return PlotPlanRequest(
        yaml_text=screenplay_to_yaml(base or plot_script()),
        instruction=kwargs.pop("instruction", "让林夏死亡，周宁从遗物中继续调查。"),
        character_id=kwargs.pop("character_id", "character-1"),
        scene_id=kwargs.pop("scene_id", "scene-2"),
        **kwargs,
    )


def scene_ledger(scene, events):
    events = [e.model_copy(deep=True) for e in events]
    coverage = []
    for i, element in enumerate(scene.elements):
        included = events if len(scene.elements) == 1 else events[i : i + 1]
        for event in included:
            event.evidence = [Evidence(scene_id=scene.id, element_index=i, quote=element.text)]
        coverage.append(
            ElementCoverage(
                element_index=i,
                event_ids=[e.id for e in included],
                context_only=not included,
                explanation="测试正文事件",
            )
        )
    return SceneLogic(scene_id=scene.id, events=events, coverage=coverage)


def fixture_logic(script):
    if script.plot_state and script.plot_state.logic:
        return script.plot_state.logic.model_copy(deep=True)
    entities = [Entity(id=c.id, name=c.name, kind="character") for c in script.characters]
    entities += [
        Entity(id="key", name="钥匙", kind="item"),
        Entity(id="letter", name="信", kind="item"),
        Entity(id="port", name="码头", kind="location"),
        Entity(id="warehouse", name="仓库", kind="location"),
        Entity(id="secret", name="身份", kind="secret"),
    ]
    values = [
        fact(c.id, p, v)
        for c in script.characters
        for p, v in [("alive", "true"), ("available", "true"), ("at", "port")]
    ]
    values += [
        fact("key", "owner", "character-1"),
        fact("letter", "owner", "character-1"),
        fact("letter", "contains", "true", "secret"),
        fact("character-2", "knows", "true", "secret"),
    ]
    seeds = [
        InitialFact(fact=f, assumption=True, explanation="明确确认的测试初始条件") for f in values
    ]
    events = [
        [Event(id="read", kind="act", actor="character-1")],
        [Event(id="give", kind="transfer", actor="character-1", target="character-2", item="key")],
        [Event(id="open", kind="use_item", actor="character-2", item="key")],
        [Event(id="look", kind="act", actor="character-1")],
    ]
    return StoryLogic(
        entities=entities,
        initial_facts=seeds,
        scenes=[scene_ledger(scene, es) for scene, es in zip(script.scenes, events)],
    )


class PlotModelStub:
    """Protocol fixture with independent plan, generator and reviewer responses."""

    def __init__(
        self,
        *,
        bad_scene=False,
        semantic_issue=False,
        audit_issue=False,
        omit_plan=False,
        future_dependency=False,
        interrupt=None,
    ):
        self.generated_events = {}
        self.calls = []
        self.contexts = []
        self.bad_scene = bad_scene
        self.semantic_issue = semantic_issue
        self.audit_issue = audit_issue
        self.omit_plan = omit_plan
        self.future_dependency = future_dependency
        self.interrupt = interrupt

    def complete_json(self, prompt, *, use_cache, prompt_id):
        assert use_cache is False
        self.calls.append(prompt_id)
        ctx = json.loads(prompt.split("上下文 JSON：", 1)[1])
        self.contexts.append((prompt_id, ctx))
        if self.interrupt == (prompt_id, ctx.get("scene", {}).get("id")):
            raise ValueError("模拟服务中断")
        if prompt_id == "plot.plan":
            scenes = ctx["screenplay"]["scenes"]
            start = next(
                i for i, s in enumerate(scenes) if s["id"] == ctx["change"]["effective_scene_id"]
            )
            result = {
                k: ctx["change"][k] for k in ("character_id", "effective_scene_id", "operation")
            }
            script = Screenplay.model_validate(ctx["screenplay"])
            result["logic"] = fixture_logic(script).model_dump(mode="json")
            result["target_event"] = event_for_change(
                PlotChange.model_validate(ctx["change"])
            ).model_dump(mode="json")
            for key in (
                "target",
                "item",
                "destination",
                "secret",
                "relationship",
                "source_character",
                "source_item",
            ):
                result["target_event"][key] = ctx["requested_parameters"].get(key, "")
            result["scenes"] = [
                dict(
                    scene_id=s["id"],
                    impact="scan",
                    rewrite=False,
                    reason="检查线索传递是否有效",
                    replacement_beats=["从遗物取得钥匙再调查"],
                )
                for s in scenes[start:]
            ]
            result["dependencies"] = [
                dict(
                    scene_id=s["id"],
                    requires=["钥匙来源"],
                    produces=["找到证据"],
                    depends_on_scene_ids=[scenes[i - 1]["id"]] if i else [],
                    evidence=[s["summary"]],
                )
                for i, s in enumerate(scenes)
            ]
            if self.omit_plan:
                result["scenes"].pop()
            if self.future_dependency:
                result["dependencies"][0]["depends_on_scene_ids"] = [scenes[-1]["id"]]
            return json.dumps(result, ensure_ascii=False)
        if prompt_id == "plot.rewrite":
            scene = copy.deepcopy(ctx["scene"])
            if self.bad_scene and scene["id"] != ctx["change"]["effective_scene_id"]:
                scene["characters_present"] = [ctx["change"]["character_id"]]
                return json.dumps(scene, ensure_ascii=False)
            change = PlotChange.model_validate(ctx["change"])
            if scene["id"] == change.effective_scene_id:
                events = SceneLogic.model_validate(ctx["original_scene_logic"]).events
                events = [e.model_copy(deep=True) for e in events]
                target = event_for_change(change)
                target.id = "changed-" + change.change_id
                events.append(target)
            elif scene["timeline_mode"] != "present":
                events = [Event(id="memory-" + scene["id"], kind="act", actor="character-1")]
            else:
                events = [
                    Event(
                        id="adapted-" + scene["id"],
                        kind="use_item",
                        actor="character-2",
                        item="key",
                    )
                ]
            self.generated_events[scene["id"]] = events
            names = {c["id"]: c["name"] for c in ctx["characters"]}
            from story2script.logic_models import OPERATION_LABELS

            texts = [
                f"{names[e.actor]}{OPERATION_LABELS.get(e.kind, '继续调查')}。" for e in events
            ]
            text = "".join(texts)
            scene["characters_present"] = list(dict.fromkeys(e.actor for e in events))
            if any(e.target for e in events):
                scene["characters_present"] += [
                    e.target
                    for e in events
                    if e.target and e.target not in scene["characters_present"]
                ]
            scene.update(
                summary=text,
                goal="保全遗物中的线索",
                conflict="仓库随时可能被封锁",
                beat="找到新的调查路径",
                subtext="同伴的缺席成为调查的动力",
                elements=[{"type": "action", "text": t} for t in texts],
                dramatization_decisions=[
                    {
                        "source_text": text,
                        "target": "action",
                        "rendering": text,
                        "reason": "改编后的行动",
                    }
                ],
            )
            return json.dumps(scene, ensure_ascii=False)
        if prompt_id == "plot.scene_review":
            sid = ctx["scene"]["id"]
            issues = []
            if self.semantic_issue and sid == "scene-3":
                issues = [{"scene_id": sid, "kind": "causality", "message": "钥匙来源没有依据"}]
            return json.dumps(
                {
                    "scene_id": sid,
                    "issues": issues,
                    "facts": [],
                    "logic": scene_ledger(
                        Scene.model_validate(ctx["scene"]),
                        self.generated_events.get(
                            sid, SceneLogic.model_validate(ctx["original_scene_logic"]).events
                        ),
                    ).model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        if prompt_id == "plot.audit":
            return json.dumps(
                {
                    "scanned_scene_ids": ctx["required_scene_ids"],
                    "issues": [
                        {"scene_id": "scene-3", "kind": "causality", "message": "间接因果仍断裂"}
                    ]
                    if self.audit_issue
                    else [],
                },
                ensure_ascii=False,
            )
        raise AssertionError(prompt_id)


def completed_revision(stub=None, base=None, **kwargs):
    stub = stub or PlotModelStub()
    plan = create_plan(plan_request(base, **kwargs), client=stub)
    result = generate_revision(plan.plan_id, str(uuid4()), client=stub)
    return plan, result, stub


def test_plan_propagates_indirect_dependencies_and_always_scans_tail():
    base = plot_script()
    plan = create_plan(plan_request(base), client=PlotModelStub())
    assert [s.scene_id for s in plan.scenes] == ["scene-2", "scene-3", "scene-4"]
    assert plan.scenes[1].impact == "indirect"
    assert plan.scenes[1].rewrite
    assert plan.base_hash == screenplay_hash(base)


@pytest.mark.parametrize(
    "options, message",
    [
        ({"omit_plan": True}, "遗漏"),
        ({"future_dependency": True}, "未来依赖"),
    ],
)
def test_plan_rejects_incomplete_or_cyclic_model_output(options, message):
    with pytest.raises(ValueError, match=message):
        create_plan(plan_request(), client=PlotModelStub(**options))


def test_alias_resolves_but_ambiguous_names_require_explicit_selection():
    plan = create_plan(plan_request(character_id="小夏"), client=PlotModelStub())
    assert plan.change.character_id == "character-1"
    with pytest.raises(ValueError, match="明确选择"):
        create_plan(plan_request(character_id=""), client=PlotModelStub())


def test_revive_requires_actual_death_in_baseline():
    plan = create_plan(
        plan_request(operation="revive", instruction="让林夏复活"), client=PlotModelStub()
    )
    with pytest.raises(ValueError, match="前提不成立"):
        check_base(plan, plot_script())


def test_death_changes_later_plot_preserves_prefix_and_stores_version():
    base = plot_script()
    before = base.model_dump_json()
    plan, revision, stub = completed_revision(base=base)
    assert revision.status == "candidate"
    assert base.model_dump_json() == before
    assert revision.screenplay.scenes[0] == base.scenes[0]
    assert "死亡" in revision.screenplay.scenes[1].summary
    assert all("character-1" not in s.characters_present for s in revision.screenplay.scenes[2:])
    assert revision.scanned_scene_ids == [s.scene_id for s in plan.scenes]
    assert revision.reasoning.valid
    assert revision.screenplay.plot_state.logic.initial_facts == plan.logic.initial_facts
    assert any(
        f.predicate == "alive" and f.value == "false" and f.source == "user"
        for f in revision.screenplay.plot_state.facts
    )
    assert load_record("revision", revision.revision_id)["base_hash"] == plan.base_hash
    after_ctx = next(
        ctx
        for kind, ctx in stub.contexts
        if kind == "plot.rewrite" and ctx["scene"]["id"] == "scene-3"
    )
    assert "死亡" in after_ctx["adapted_revision"]["prefix"][-1]["summary"]
    assert after_ctx["source_reference"]["namespace"] == "original_script"
    assert after_ctx["adapted_revision"]["revision_id"] == revision.revision_id
    assert screenplay_from_yaml(revision.yaml_text) == revision.screenplay


def test_exit_and_last_scene_have_no_off_by_one():
    plan, result, _ = completed_revision(operation="exit", scene_id="scene-4")
    assert result.status == "candidate"
    assert result.scanned_scene_ids == ["scene-4"]
    assert result.screenplay.scenes[:3] == result.base_screenplay.scenes[:3]
    assert any(
        f.predicate == "available" and f.value == "false"
        for f in result.screenplay.plot_state.facts
    )


@pytest.mark.parametrize(
    "options", [{"bad_scene": True}, {"semantic_issue": True}, {"audit_issue": True}]
)
def test_failed_checks_never_produce_adoptable_version(options):
    _, result, _ = completed_revision(PlotModelStub(**options))
    assert result.status == "needs_review"
    assert result.issues
    with pytest.raises(ValueError, match="不能采用"):
        switch_version(
            result.revision_id,
            PlotVersionRequest(yaml_text=screenplay_to_yaml(result.base_screenplay)),
        )


def test_adoption_rollback_and_stale_edit_guard():
    _, revision, _ = completed_revision()
    original = screenplay_to_yaml(revision.base_screenplay)
    adopted = switch_version(revision.revision_id, PlotVersionRequest(yaml_text=original))
    assert adopted.screenplay == revision.screenplay
    edited = adopted.screenplay.model_copy(deep=True)
    edited.scenes[-1].summary = "用户刚刚做的新编辑"
    with pytest.raises(HTTPException) as error:
        switch_version(
            revision.revision_id,
            PlotVersionRequest(yaml_text=screenplay_to_yaml(edited)),
            rollback=True,
        )
    assert error.value.status_code == 409
    rolled = switch_version(
        revision.revision_id, PlotVersionRequest(yaml_text=adopted.yaml_text), rollback=True
    )
    assert rolled.screenplay == revision.base_screenplay
    assert load_record("revision", revision.revision_id)["status"] == "rolled_back"


def test_stale_plan_and_repeated_death_are_rejected():
    plan, result, _ = completed_revision()
    with pytest.raises(ValueError, match="发生变化"):
        check_base(plan, result.screenplay)
    repeated = create_plan(plan_request(result.screenplay), client=PlotModelStub())
    with pytest.raises(ValueError, match="前提不成立"):
        check_base(repeated, result.screenplay)


def test_successive_changes_keep_prior_constraints():
    _, first, _ = completed_revision()
    plan, second, _ = completed_revision(
        base=first.screenplay,
        character_id="character-2",
        scene_id="scene-4",
        instruction="让周宁在此场死亡",
    )
    assert plan.parent_revision_id == first.revision_id
    assert second.parent_revision_id == first.revision_id
    assert len(second.screenplay.plot_state.changes) == 2
    assert second.screenplay.scenes[:3] == first.screenplay.scenes[:3]


@pytest.mark.parametrize("timeline_mode", ["flashback", "recording", "dream"])
def test_past_appearances_allowed_only_with_explicit_earlier_time(timeline_mode):
    base = plot_script()
    change = PlotChange(
        change_id="c",
        instruction="死亡",
        character_id="character-1",
        effective_scene_id="scene-2",
        operation="death",
    )
    base.scenes[-1].timeline_mode = timeline_mode
    base.scenes[-1].event_time_scene_id = "scene-1"
    assert not hard_issues(base, base, [change])
    base.scenes[-1].event_time_scene_id = "scene-3"
    assert hard_issues(base, base, [change])


def test_mention_of_dead_character_is_allowed_without_present_or_dialogue():
    base = plot_script()
    base.scenes[-1].characters_present = ["character-2"]
    base.scenes[-1].elements[0].text = "周宁想起林夏，但他已经无法回来。"
    change = PlotChange(
        change_id="c",
        instruction="死亡",
        character_id="character-1",
        effective_scene_id="scene-2",
        operation="death",
    )
    assert not hard_issues(base, base, [change])


def test_unmarked_flashback_prevents_generation():
    base = plot_script()
    base.scenes[-1].summary = "闪回：林夏发现钥匙。"
    plan = create_plan(plan_request(base), client=PlotModelStub())
    assert plan.unresolved_questions
    with pytest.raises(ValueError, match="待确认"):
        generate_revision(plan.plan_id, "blocked", client=PlotModelStub())


def test_checkpoint_resume_skips_completed_scenes_and_keeps_call_budget():
    stub = PlotModelStub(interrupt=("plot.scene_review", "scene-3"))
    plan = create_plan(plan_request(), client=stub)
    job_id = str(uuid4())
    with pytest.raises(ValueError, match="模拟服务中断"):
        generate_revision(plan.plan_id, job_id, client=stub)
    checkpoint = load_record("checkpoint", job_id)
    assert checkpoint["done"] == ["scene-2"]
    assert checkpoint["logic"]["scene-2"]["events"][-1]["kind"] == "death"
    previous_calls = checkpoint["calls"]
    replacement = PlotModelStub()
    result = generate_revision(plan.plan_id, job_id, client=replacement)
    assert result.status == "candidate"
    assert result.reasoning.valid
    assert all(ctx.get("scene", {}).get("id") != "scene-2" for _, ctx in replacement.contexts)
    assert result.calls == previous_calls + len(replacement.calls)


def test_recovery_after_atomic_revision_save_does_not_call_model_twice():
    plan, result, _ = completed_revision()
    stub = PlotModelStub()
    loaded = generate_revision(
        plan.plan_id, result.revision_id.removeprefix("revision-"), client=stub
    )
    assert loaded == result
    assert stub.calls == []


def test_budget_exhaustion_cannot_be_reset_by_resume():
    plan = create_plan(plan_request(), client=PlotModelStub())
    job_id = str(uuid4())
    save_record(
        "checkpoint",
        job_id,
        {
            "calls": plan.max_calls,
            "elapsed": 0,
            "done": [],
            "scenes": {},
            "facts": [],
            "attempts": {},
        },
    )
    stub = PlotModelStub()
    result = generate_revision(plan.plan_id, job_id, client=stub)
    assert result.status == "needs_review"
    assert stub.calls == []


def test_plot_http_error_contract_and_source_hash():
    client = TestClient(app)
    assert client.get("/api/plot/jobs/missing").status_code == 404
    assert client.get("/api/plot/revisions/missing").status_code == 404
    assert (
        client.post(
            "/api/plot/plan",
            json={"yaml_text": "x: [", "instruction": "死亡", "scene_id": "scene-1"},
        ).status_code
        == 422
    )
    plan = create_plan(plan_request(), client=PlotModelStub())
    changed = plot_script()
    changed.scenes[0].summary = "新内容"
    response = client.post(
        "/api/plot/jobs", json={"plan_id": plan.plan_id, "yaml_text": screenplay_to_yaml(changed)}
    )
    assert response.status_code == 422


def test_local_rewrite_cannot_reintroduce_exited_character():
    from story2script.scene_rewrite import rewrite_scene

    base = plot_script()
    change = PlotChange(
        change_id="c",
        instruction="死亡",
        character_id="character-1",
        effective_scene_id="scene-2",
        operation="death",
    )
    base.plot_state = PlotContinuity(revision_id="r", changes=[change])
    base.scenes[-1].characters_present = ["character-2"]
    with pytest.raises(ValueError, match="违反剧情约束"):
        rewrite_scene(base, "scene-4", "adjust_character_voice", character_id="character-1")


def test_durable_job_http_roundtrip_and_restart(monkeypatch, tmp_path):
    import story2script.plot_api as api_module
    import story2script.plot_jobs as jobs_module
    from story2script.plot_jobs import PlotJobStore

    store = PlotJobStore(db_path=tmp_path / "jobs.db")
    monkeypatch.setattr(api_module, "plot_jobs", store)
    monkeypatch.setattr(
        api_module, "create_plan", lambda request: create_plan(request, client=PlotModelStub())
    )
    monkeypatch.setattr(
        jobs_module,
        "generate_revision",
        lambda plan_id, job_id, progress: generate_revision(
            plan_id, job_id, progress, client=PlotModelStub()
        ),
    )
    client = TestClient(app)
    plan_response = client.post("/api/plot/plan", json=plan_request().model_dump(mode="json"))
    assert plan_response.status_code == 200
    plan_id = plan_response.json()["plan_id"]
    response = client.post(
        "/api/plot/jobs",
        json={
            "plan_id": plan_id,
            "yaml_text": screenplay_to_yaml(plot_script()),
            "confirm_logic": True,
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    store._executor.shutdown(wait=True)
    snapshot = client.get(f"/api/plot/jobs/{job_id}").json()
    assert snapshot["status"] == "succeeded"
    revision = snapshot["result"]
    adopted = client.post(
        f"/api/plot/revisions/{revision['revision_id']}/adopt",
        json={"yaml_text": screenplay_to_yaml(plot_script())},
    )
    assert adopted.status_code == 200
    reloaded = PlotJobStore(db_path=tmp_path / "jobs.db")
    assert reloaded.snapshot(job_id).result.revision_id == revision["revision_id"]
    reloaded._executor.shutdown(wait=True)
    rolled = client.post(
        f"/api/plot/revisions/{revision['revision_id']}/rollback",
        json={"yaml_text": adopted.json()["yaml_text"]},
    )
    assert rolled.status_code == 200
    assert screenplay_from_yaml(rolled.json()["yaml_text"]) == plot_script()


def test_original_novel_retrieval_is_separated_from_adapted_facts():
    source = "第一章\n林夏在车站找到信。\n第二章\n林夏把钥匙交给周宁。\n第三章\n林夏拿走仓库证据。"
    stub = PlotModelStub()
    request = plan_request(novel_text=source)
    plan = create_plan(request, client=stub)
    generate_revision(plan.plan_id, str(uuid4()), client=stub)
    contexts = [ctx for kind, ctx in stub.contexts if kind == "plot.rewrite"]
    assert all(ctx["source_novel"]["evidence"] for ctx in contexts)
    assert contexts[1]["source_novel"]["source_hash"]
    assert any(
        f["predicate"] == "alive" and f["value"] == "false"
        for f in contexts[1]["adapted_revision"]["facts"]
    )
    assert contexts[1]["source_novel"]["namespace"] == "source_novel"


def test_scene_reviewer_cannot_override_user_death_fact():
    class ContradictoryReviewer(PlotModelStub):
        def complete_json(self, prompt, **kwargs):
            raw = super().complete_json(prompt, **kwargs)
            if kwargs["prompt_id"] == "plot.scene_review":
                response = json.loads(raw)
                response["facts"].append(
                    {
                        "subject": "character-1",
                        "predicate": "alive",
                        "value": "true",
                        "valid_from_scene_id": response["scene_id"],
                        "source": "scene",
                    }
                )
                return json.dumps(response)
            return raw

    _, result, _ = completed_revision(ContradictoryReviewer())
    assert result.status == "needs_review"
    assert any("事实" in issue.message for issue in result.issues)


def test_flashback_context_uses_historical_fact_time():
    base = plot_script()
    base.scenes[-1].timeline_mode = "flashback"
    base.scenes[-1].event_time_scene_id = "scene-1"
    _, result, stub = completed_revision(base=base)
    assert result.status == "candidate"
    context = next(
        ctx
        for kind, ctx in stub.contexts
        if kind == "plot.rewrite" and ctx["scene"]["id"] == "scene-4"
    )
    assert context["adapted_revision"]["facts_as_of_scene_start"] == "scene-1"
    assert any(
        f["subject"] == "character-1" and f["predicate"] == "alive" and f["value"] == "true"
        for f in context["adapted_revision"]["facts"]
    )
    assert not any(f.valid_from_scene_id == "scene-4" for f in result.screenplay.plot_state.facts)


@pytest.mark.parametrize(
    "operation,slots",
    [
        ("transfer", {"character_id": "character-2", "target": "character-1", "item": "key"}),
        ("move", {"destination": "warehouse"}),
        ("learn", {"secret": "secret", "source_item": "letter"}),
        ("relationship", {"target": "character-2", "relationship": "enemy"}),
    ],
)
def test_non_lifecycle_changes_generate_and_adopt(operation, slots):
    plan, result, _ = completed_revision(
        operation=operation, instruction="执行指定事件并联动后续剧情", scene_id="scene-4", **slots
    )
    assert result.status == "candidate", result.issues
    assert result.reasoning.valid
    assert result.screenplay.plot_state.logic.initial_facts == plan.logic.initial_facts
    adopted = switch_version(
        result.revision_id, PlotVersionRequest(yaml_text=plan_request().yaml_text)
    )
    assert adopted.screenplay == result.screenplay


@pytest.mark.parametrize("outgoing,incoming", [("death", "revive"), ("exit", "return")])
def test_later_explicit_recovery_keeps_earlier_lifecycle_event(outgoing, incoming):
    _, first, _ = completed_revision(operation=outgoing)
    _, second, _ = completed_revision(
        base=first.screenplay,
        operation=incoming,
        scene_id="scene-4",
        instruction="让林夏明确恢复行动",
    )
    assert second.status == "candidate", second.issues
    assert second.reasoning.valid
    assert second.screenplay.plot_state.changes[0].operation == outgoing
    assert any(
        f.subject == "character-1" and f.predicate == "available" and f.value == "true"
        for f in second.reasoning.final_facts
    )


def test_empty_model_issues_cannot_hide_unauthorized_revival():
    class LyingReviewer(PlotModelStub):
        def complete_json(self, prompt, **kwargs):
            response = json.loads(super().complete_json(prompt, **kwargs))
            if kwargs["prompt_id"] == "plot.scene_review" and response["scene_id"] == "scene-3":
                event_data = response["logic"]["events"][0]
                event_data.update(kind="revive", actor="character-1", item="")
                assert response["issues"] == []
            return json.dumps(response, ensure_ascii=False)

    _, result, _ = completed_revision(LyingReviewer())
    assert result.status == "needs_review"
    assert any("显式复活" in i.message for i in result.issues)


def test_logic_editor_preserves_original_plan_and_requires_confirmation():
    client = TestClient(app)
    plan = create_plan(plan_request(), client=PlotModelStub())
    original = load_record("plan", plan.plan_id)
    response = client.post(
        "/api/plot/jobs", json={"plan_id": plan.plan_id, "yaml_text": plan_request().yaml_text}
    )
    assert response.status_code == 422 and "确认初始事实" in response.json()["detail"]
    logic = plan.logic.model_copy(deep=True)
    logic.initial_facts = [f for f in logic.initial_facts if f.fact.subject != "key"]
    response = client.post(
        f"/api/plot/plans/{plan.plan_id}/logic", json={"logic": logic.model_dump(mode="json")}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["plan_id"] != plan.plan_id
    assert not updated["baseline_reasoning"]["valid"]
    assert load_record("plan", plan.plan_id) == original
    assert (
        client.post(
            "/api/plot/jobs",
            json={
                "plan_id": updated["plan_id"],
                "yaml_text": plan_request().yaml_text,
                "confirm_logic": True,
            },
        ).status_code
        == 422
    )
    fixed = client.post(
        f"/api/plot/plans/{updated['plan_id']}/logic",
        json={"logic": plan.logic.model_dump(mode="json")},
    )
    assert fixed.json()["baseline_reasoning"]["valid"]
    checked = client.post(
        "/api/plot/logic/check",
        json={"yaml_text": plan_request().yaml_text, "logic": plan.logic.model_dump(mode="json")},
    )
    assert checked.status_code == 200 and checked.json()["valid"]


def test_adoption_replays_rules_instead_of_trusting_saved_pass_flag():
    _, result, _ = completed_revision()
    saved = load_record("revision", result.revision_id)
    saved["screenplay"]["scenes"][-1]["characters_present"].append("character-1")
    save_record("revision", result.revision_id, saved)
    with pytest.raises(ValueError, match="确定性规则重检"):
        switch_version(result.revision_id, PlotVersionRequest(yaml_text=plan_request().yaml_text))


def test_initial_citations_survive_rewritten_scene_as_frozen_source():
    class CitedInitial(PlotModelStub):
        def complete_json(self, prompt, **kwargs):
            response = json.loads(super().complete_json(prompt, **kwargs))
            if kwargs["prompt_id"] == "plot.plan":
                seed = response["logic"]["initial_facts"][0]
                seed["evidence"] = [
                    {"scene_id": "scene-2", "element_index": 0, "quote": "林夏把钥匙交给周宁"}
                ]
                seed["assumption"] = (
                    True  # Citation does not itself establish the opening timeline.
                )
            return json.dumps(response, ensure_ascii=False)

    plan, result, _ = completed_revision(CitedInitial())
    assert result.status == "candidate", result.issues
    assert result.reasoning.valid
    assert result.screenplay.plot_state.logic.initial_source_hash == plan.base_hash
    assert result.screenplay.plot_state.logic.initial_source == plan.logic.initial_source


def test_scene_reviewer_cannot_inject_initial_facts():
    class MutatingReviewer(PlotModelStub):
        def complete_json(self, prompt, **kwargs):
            response = json.loads(super().complete_json(prompt, **kwargs))
            if kwargs["prompt_id"] == "plot.scene_review":
                response["logic"]["initial_facts"] = []
            return json.dumps(response, ensure_ascii=False)

    stub = MutatingReviewer()
    plan = create_plan(plan_request(), client=stub)
    with pytest.raises(ValueError, match="Extra inputs"):
        generate_revision(plan.plan_id, str(uuid4()), client=stub)


def test_local_rewrite_cannot_leave_stale_logic_and_pure_camera_edit_is_allowed():
    from story2script.plot_revision import check_local_logic

    _, result, _ = completed_revision()
    base = result.screenplay
    edited = base.model_copy(deep=True)
    edited.scenes[-1].summary += " 镜头远移。"
    check_local_logic(base, edited, "scene-4")
    edited.scenes[-1].elements[0].text = "周宁突然离场。"
    with pytest.raises(ValueError, match="已有事件账本"):
        check_local_logic(base, edited, "scene-4")


def test_ai_local_check_refreshes_citations_and_rejects_new_state(monkeypatch):
    import story2script.plot_revision as module

    _, result, _ = completed_revision()
    base = result.screenplay
    edited = base.model_copy(deep=True)
    edited.scenes[-1].elements[0].text = "周宁用钥匙缓缓开门。"
    ledger = scene_ledger(
        edited.scenes[-1], [Event(id="local-use", actor="character-2", kind="use_item", item="key")]
    )

    class LocalModel:
        def __init__(self, **kwargs):
            pass

        def complete_json(self, prompt, **kwargs):
            return json.dumps(
                {
                    "scene_id": "scene-4",
                    "issues": [],
                    "facts": [],
                    "logic": ledger.model_dump(mode="json"),
                }
            )

    monkeypatch.setattr(module, "LLMClient", lambda **kwargs: LocalModel(**kwargs))
    # CallBudget checks concrete LLMClient type; supply a no-network call wrapper.
    monkeypatch.setattr(
        module.CallBudget,
        "call",
        lambda self, task, model, ctx, pid: model.model_validate_json(
            self.client.complete_json(task)
        ),
    )
    module.check_local_logic(base, edited, "scene-4", "ai", client=object())
    assert edited.plot_state.logic.scenes[-1].events[0].evidence[0].quote == "周宁用钥匙缓缓开门。"
    ledger.events[0].kind = "exit"
    ledger.events[0].item = ""
    with pytest.raises(ValueError, match="改变了剧情状态"):
        module.check_local_logic(base, edited, "scene-4", "ai", client=object())
