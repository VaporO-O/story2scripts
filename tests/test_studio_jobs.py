import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import story2script.studio_api as api_module
import story2script.studio_jobs as jobs_module
from story2script.creative_models import EditingState
from story2script.job_store import JobConflictError, JobStorageError
from story2script.main import app
from story2script.plot_state import screenplay_hash
from story2script.plot_store import load_record, save_record
from story2script.studio import balanced_groups
from story2script.studio_jobs import StudioJobStore
from story2script.studio_models import StudioJobRequest
from story2script.yaml_export import screenplay_from_yaml, screenplay_to_yaml
from test_job_store import wait_for
from test_plot_revision import completed_revision, plot_script
from test_studio import dialogue_script


class Model:
    def __init__(
        self,
        script,
        *,
        fail_scene="",
        started=None,
        release=None,
        bad_emotion=False,
        bad_audit=False,
    ):
        self.script = script
        self.calls = []
        self.fail_scene = fail_scene
        self.started, self.release = started, release
        self.bad_emotion, self.bad_audit = bad_emotion, bad_audit
        self.client = SimpleNamespace(close=lambda: None)

    def complete_json(self, prompt, *, use_cache, prompt_id):
        assert use_cache is False
        context = json.loads(prompt.split("上下文 JSON：", 1)[1])
        sid = context.get("scene", {}).get("id", "")
        self.calls.append((prompt_id, sid))
        if self.started:
            self.started.set()
            assert self.release.wait(10)
        if self.fail_scene and sid == self.fail_scene:
            self.fail_scene = ""
            raise ValueError("模拟模型中断")
        if prompt_id == "studio.structure":
            result = {
                "groups": [
                    g.model_dump()
                    for g in balanced_groups(self.script, context["kind"], context["count"])
                ]
            }
        elif prompt_id == "studio.emotions":
            notes = [{k: v for k, v in n.items() if k != "source"} for n in context["dialogue"]]
            if self.bad_emotion:
                notes[0]["quote"] = "模型编造的引用"
            result = {"notes": notes}
        elif prompt_id == "studio.scene_recheck":
            scene = next(s for s in self.script.plot_state.logic.scenes if s.scene_id == sid)
            result = {
                "scene_id": sid,
                "issues": [],
                "facts": [],
                "logic": scene.model_dump(mode="json"),
            }
        elif prompt_id == "studio.audit":
            result = {
                "scanned_scene_ids": [s.id for s in self.script.scenes],
                "issues": [{"scene_id": "scene-4", "kind": "causality", "message": "剧情仍有矛盾"}]
                if self.bad_audit
                else [],
            }
        else:
            raise AssertionError(prompt_id)
        return json.dumps(result, ensure_ascii=False)


def request(operation="structure", script=None, **parameters):
    params = {"yaml_text": screenplay_to_yaml(script or plot_script()), "mode": "ai", **parameters}
    if operation == "structure":
        params.setdefault("count", 2)
    return StudioJobRequest(operation=operation, parameters=params)


@pytest.fixture
def make_store(tmp_path, monkeypatch):
    stores = []

    def make(model, name="studio.db"):
        monkeypatch.setattr(jobs_module, "LLMClient", lambda **kwargs: model)
        store = StudioJobStore(db_path=tmp_path / name)
        stores.append(store)
        monkeypatch.setattr(api_module, "studio_jobs", store)
        return store

    yield make
    for store in stores:
        store._executor.shutdown(wait=True)


def test_async_endpoint_returns_while_model_is_still_running(make_store):
    started, release = threading.Event(), threading.Event()
    model = Model(plot_script(), started=started, release=release)
    store = make_store(model)
    try:
        response = TestClient(app).post(
            "/api/studio/jobs",
            json=request().model_dump(mode="json"),
            headers={"Idempotency-Key": "click-1"},
        )
        assert response.status_code == 202
        assert started.wait(10)
        job_id = response.json()["job_id"]
        assert store.snapshot(job_id).status == "running"
        release.set()
        result = wait_for(store, job_id)
        assert result.status == "succeeded" and result.result.operation == "structure"
        assert len(result.result.payload["screenplay"]["structure"]) == 2
        assert result.calls == 1
    finally:
        release.set()


@pytest.mark.parametrize("operation", ["structure", "emotions", "recheck"])
def test_legacy_ai_endpoints_are_202_and_local_modes_remain_immediate(make_store, operation):
    s = dialogue_script()
    store = make_store(Model(s))
    payload = request(operation, s).parameters
    response = TestClient(app).post(f"/api/studio/{operation}", json=payload)
    assert response.status_code == 202
    assert wait_for(store, response.json()["job_id"]).status == "succeeded"
    payload["mode"] = {"structure": "balanced", "emotions": "annotated", "recheck": "rules"}[
        operation
    ]
    immediate = TestClient(app).post(f"/api/studio/{operation}", json=payload)
    assert immediate.status_code == 200 and "job_id" not in immediate.json()


def test_checkpoint_resume_skips_previous_response_and_keeps_budget(make_store):
    _, revision, _ = completed_revision()
    s = revision.screenplay
    s.editing = EditingState(
        based_on_hash=screenplay_hash(s), dirty_scene_ids=["scene-3", "scene-4"]
    )
    model = Model(s, fail_scene="scene-4")
    store = make_store(model)
    req = request("recheck", s)
    req.max_calls = 5
    job_id = store.create(req).job_id
    first = wait_for(store, job_id)
    assert first.status == "failed" and first.calls == 2
    cp = load_record("studio_checkpoint", job_id, db_path=store._db_path())
    assert len(cp["responses"]) == 1
    store.resume(job_id)
    second = wait_for(store, job_id)
    assert second.status == "succeeded", second.error
    assert second.calls == 4 and second.attempts == 2
    assert model.calls.count(("studio.scene_recheck", "scene-3")) == 1
    assert model.calls.count(("studio.scene_recheck", "scene-4")) == 2
    assert second.result.payload["screenplay"]["editing"]["status"] == "checked"
    assert second.result.duration_seconds >= first.elapsed_seconds


def test_completion_commit_failure_recovers_saved_result_without_new_model_call(
    make_store, monkeypatch
):
    model = Model(plot_script())
    store = make_store(model)
    real = store._persist

    def fail_once(row):
        if row["status"] == "succeeded":
            raise JobStorageError("模拟完成时存储故障")
        return real(row)

    monkeypatch.setattr(store, "_persist", fail_once)
    job_id = store.create(request()).job_id
    assert wait_for(store, job_id).status == "failed"
    assert load_record("studio_checkpoint", job_id, db_path=store._db_path())["result"]
    before = len(model.calls)
    monkeypatch.setattr(store, "_persist", real)
    store.resume(job_id)
    assert wait_for(store, job_id).status == "succeeded"
    assert len(model.calls) == before


def test_studio_job_restore_history_input_and_request_lookup(make_store):
    store = make_store(Model(plot_script()))
    job_id = store.create(request(), idempotency_key="lost-response").job_id
    wait_for(store, job_id)
    client = TestClient(app)
    assert client.get("/api/studio/jobs/requests/lost-response").json()["job_id"] == job_id
    assert any(j["job_id"] == job_id for j in client.get("/api/studio/jobs").json()["jobs"])
    frozen = client.get(f"/api/studio/jobs/{job_id}/input").json()
    assert screenplay_from_yaml(frozen["yaml_text"]) == plot_script()
    assert frozen["request"]["operation"] == "structure"
    assert client.get("/api/studio/jobs/missing").status_code == 404


def test_studio_response_survives_store_reopen(make_store):
    model = Model(plot_script())
    store = make_store(model)
    job_id = store.create(request()).job_id
    before = wait_for(store, job_id)
    reopened = make_store(model)
    assert reopened.snapshot(job_id).result == before.result
    assert len(model.calls) == 1


def test_job_retries_keep_call_and_time_limits(make_store):
    model = Model(dialogue_script(), bad_emotion=True)
    store = make_store(model)
    req = request("emotions", model.script)
    req.max_calls = 1
    job_id = store.create(req).job_id
    assert wait_for(store, job_id).status == "failed"
    # Structurally valid but semantically invalid saved responses do not get a free retry.
    store.resume(job_id)
    second = wait_for(store, job_id)
    assert second.status == "failed" and second.calls == 1
    store.resume(job_id)
    third = wait_for(store, job_id)
    assert third.status == "failed" and third.attempts == 3
    with pytest.raises(ValueError, match="上限"):
        store.resume(job_id)
    assert len(model.calls) == 1


def test_cumulative_timeout_cannot_reset_on_resume(make_store):
    _, revision, _ = completed_revision()
    s = revision.screenplay
    s.editing = EditingState(based_on_hash=screenplay_hash(s), dirty_scene_ids=["scene-4"])
    model = Model(s, fail_scene="scene-4")
    store = make_store(model)
    req = request("recheck", s)
    job_id = store.create(req).job_id
    assert wait_for(store, job_id).status == "failed"
    cp = load_record("studio_checkpoint", job_id, db_path=store._db_path())
    cp["elapsed"] = req.timeout_seconds + 1
    save_record("studio_checkpoint", job_id, cp, db_path=store._db_path())
    before = len(model.calls)
    store.resume(job_id)
    assert wait_for(store, job_id).status == "failed"
    assert len(model.calls) == before


def test_semantic_failure_is_successful_check_with_invalid_payload(make_store):
    model = Model(plot_script(), bad_audit=True)
    store = make_store(model)
    job_id = store.create(request("recheck", model.script)).job_id
    result = wait_for(store, job_id)
    assert result.status == "succeeded" and result.result.payload["valid"] is False
    assert "未解决问题" in result.message
    assert result.result.payload["screenplay"]["editing"]["status"] == "needs_check"


def test_request_validation_happens_before_background_dispatch(make_store):
    model = Model(plot_script())
    store = make_store(model)
    with pytest.raises(ValueError, match="不能超过"):
        store.create(request(count=20))
    client = TestClient(app)
    bad = request().model_dump(mode="json")
    bad["parameters"]["unrecognized"] = "arbitrary"
    assert client.post("/api/studio/jobs", json=bad).status_code == 422
    assert model.calls == []


def test_duplicate_studio_request_does_not_dispatch_twice(make_store):
    model = Model(plot_script())
    store = make_store(model)
    first = store.create(request(), idempotency_key="same").job_id
    second = store.create(request(), idempotency_key="same").job_id
    assert first == second
    assert wait_for(store, first).status == "succeeded"
    assert len(model.calls) == 1
    with pytest.raises(JobConflictError):
        store.create(request(count=3), idempotency_key="same")


def test_cancel_queued_studio_job_does_not_call_model(make_store, monkeypatch):
    model = Model(plot_script())
    store = make_store(model)
    monkeypatch.setattr(store, "_enqueue", lambda job_id: None)
    job_id = store.create(request()).job_id
    result = TestClient(app).post(f"/api/studio/jobs/{job_id}/cancel")
    assert result.status_code == 200 and result.json()["stage"] == "已取消"
    store._execute(job_id)
    assert model.calls == []
    assert TestClient(app).post(f"/api/studio/jobs/{job_id}/resume").status_code == 422


def test_invalid_grounding_response_can_be_replaced_within_remaining_budget(make_store):
    model = Model(dialogue_script(), bad_emotion=True)
    store = make_store(model)
    job_id = store.create(request("emotions", model.script)).job_id
    assert wait_for(store, job_id).status == "failed"
    cp = load_record("studio_checkpoint", job_id, db_path=store._db_path())
    assert not cp["responses"] and cp["calls"] == 1
    model.bad_emotion = False
    store.resume(job_id)
    result = wait_for(store, job_id)
    assert result.status == "succeeded" and result.calls == 2


def test_empty_dialogue_analysis_does_not_require_model_configuration(make_store, monkeypatch):
    store = make_store(Model(plot_script()))

    def must_not_create(**kwargs):
        raise AssertionError("empty dialogue must not create an LLM client")

    monkeypatch.setattr(jobs_module, "LLMClient", must_not_create)
    result = wait_for(store, store.create(request("emotions")).job_id)
    assert result.status == "succeeded" and result.calls == 0
    assert result.result.payload["notes"] == []


def test_unfinished_call_time_is_conservatively_charged_after_crash():
    from story2script.studio_jobs import CheckpointBudget

    req = request()
    req.timeout_seconds = 30
    cp = {
        "calls": 1,
        "elapsed": 2,
        "responses": {},
        "in_flight": {"elapsed_before": 2, "reserved_seconds": 28},
    }
    model = Model(plot_script())
    saved = []
    budget = CheckpointBudget(
        model, req, cp, lambda p: saved.append(dict(p)), lambda *args: None, 1
    )
    assert cp["elapsed"] == 30 and "in_flight" not in cp
    from story2script.studio_models import StructureProposal

    with pytest.raises(ValueError, match="时间预算"):
        budget.call("task", StructureProposal, {}, "studio.structure")
    assert model.calls == [] and saved
