import json
import sqlite3
from uuid import uuid4
from yaml import YAMLError

from fastapi import APIRouter, HTTPException, Header

from .plot_jobs import plot_jobs
from .plot_models import (
    PlotJobRequest,
    PlotJobStatus,
    PlotPlanRequest,
    PlotVersionRequest,
    PlotVersionResponse,
    ScriptRevision,
    LogicCheckRequest,
    LogicPlanUpdate,
)
from .logic_engine import reason_story
from .logic_integration import (
    analyze_plan,
    capture_initial_source,
    verify_goal,
    preserve_registered_rules,
)
from .plot_revision import create_plan, load_plan
from .plot_state import RevisionPlan, screenplay_hash
from .plot_store import load_record, save_record, plot_connection
from .security import redact_secrets
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml

router = APIRouter(prefix="/api/plot", tags=["plot"])


def _error(exc):
    if isinstance(exc, KeyError):
        return HTTPException(404, "剧情计划、任务或版本不存在。")
    if isinstance(exc, sqlite3.Error):
        return HTTPException(503, "剧情版本存储暂不可用，请重试。")
    return HTTPException(422, redact_secrets(str(exc)))


@router.post("/plan", response_model=RevisionPlan)
def plan_plot(request: PlotPlanRequest):
    try:
        return create_plan(request)
    except (ValueError, KeyError, sqlite3.Error, YAMLError) as exc:
        raise _error(exc) from exc


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    try:
        plan, base = load_plan(plan_id)
        return {"plan": plan, "base_yaml": screenplay_to_yaml(base), "base_screenplay": base}
    except (ValueError, KeyError, sqlite3.Error) as exc:
        raise _error(exc) from exc


@router.post("/logic/check")
def check_logic(request: LogicCheckRequest):
    try:
        script = screenplay_from_yaml(request.yaml_text)
        return reason_story(
            script, request.logic, script.plot_state.changes if script.plot_state else []
        )
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.post("/plans/{plan_id}/logic", response_model=RevisionPlan)
def update_logic(plan_id: str, request: LogicPlanUpdate):
    try:
        saved = load_record("plan", plan_id)
        plan, base = load_plan(plan_id)
        plan.logic = capture_initial_source(request.logic.model_copy(deep=True), base)
        preserve_registered_rules(plan.logic, base)
        plan.plan_id = str(uuid4())
        analyze_plan(plan, base)
        saved["plan"] = plan.model_dump(mode="json")
        save_record("plan", plan.plan_id, saved)
        return plan
    except (ValueError, KeyError, sqlite3.Error) as exc:
        raise _error(exc) from exc


@router.post("/jobs", response_model=PlotJobStatus)
def start_plot_job(request: PlotJobRequest, idempotency_key: str = Header(default="")):
    try:
        return plot_jobs.create(request, idempotency_key=idempotency_key)
    except (ValueError, KeyError, sqlite3.Error, YAMLError) as exc:
        raise _error(exc) from exc


@router.get("/jobs/{job_id}", response_model=PlotJobStatus)
def get_plot_job(job_id: str):
    try:
        return plot_jobs.snapshot(job_id)
    except KeyError as exc:
        raise _error(exc) from exc


@router.post("/jobs/{job_id}/resume", response_model=PlotJobStatus)
def resume_plot_job(job_id: str):
    try:
        return plot_jobs.resume(job_id)
    except (ValueError, KeyError, sqlite3.Error) as exc:
        raise _error(exc) from exc


@router.post("/jobs/{job_id}/cancel", response_model=PlotJobStatus)
def cancel_plot_job(job_id: str):
    try:
        plot_jobs.cancel(job_id)
        return plot_jobs.snapshot(job_id)
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.get("/revisions/{revision_id}", response_model=ScriptRevision)
def get_revision(revision_id: str):
    try:
        return ScriptRevision.model_validate(load_record("revision", revision_id))
    except (ValueError, KeyError, sqlite3.Error) as exc:
        raise _error(exc) from exc


def switch_version(revision_id: str, request: PlotVersionRequest, rollback=False):
    current = screenplay_from_yaml(request.yaml_text)
    with plot_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload FROM plot_records WHERE kind='revision' AND id=?", (revision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        revision = ScriptRevision.model_validate_json(row[0])
        current_hash = screenplay_hash(current)
        if revision.issues or revision.status == "needs_review":
            raise ValueError("候选版本存在未解决的一致性问题，不能采用。")
        if rollback:
            if current_hash != revision.result_hash:
                raise HTTPException(409, "当前剧本已变化，不能回退覆盖新编辑。")
            result = revision.base_screenplay
            revision.status = "rolled_back"
        else:
            if current_hash not in (revision.base_hash, revision.result_hash):
                raise HTTPException(409, "当前剧本已变化，不能采用旧候选覆盖新编辑。")
            logic = revision.screenplay.plot_state.logic if revision.screenplay.plot_state else None
            frozen = revision.plan.logic
            if logic is None or frozen is None:
                raise ValueError("旧候选缺少规则校验账本，请重新规划后生成。")
            if any(
                getattr(logic, key) != getattr(frozen, key)
                for key in (
                    "entities",
                    "initial_facts",
                    "initial_source",
                    "initial_source_hash",
                    "custom_rules",
                    "custom_properties",
                )
            ):
                raise ValueError("候选改动了已确认的初始事实或实体，不能采用。")
            report = reason_story(
                revision.screenplay, logic, revision.screenplay.plot_state.changes
            )
            if not report.valid or not verify_goal(report, logic, revision.plan.change):
                raise ValueError("候选版本未通过确定性规则重检，不能采用。")
            if screenplay_hash(revision.screenplay) != revision.result_hash:
                raise ValueError("候选版本内容与保存的校验值不一致，不能采用。")
            revision.reasoning = report
            result = revision.screenplay
            revision.status = "adopted"
        conn.execute(
            "UPDATE plot_records SET payload=? WHERE kind='revision' AND id=?",
            (json.dumps(revision.model_dump(mode="json"), ensure_ascii=False), revision_id),
        )
    return PlotVersionResponse(
        revision_id=result.plot_state.revision_id if result.plot_state else "",
        screenplay=result,
        yaml_text=screenplay_to_yaml(result),
        message="已回退到改写前版本。" if rollback else "已采用候选版本。",
    )


@router.post("/revisions/{revision_id}/adopt", response_model=PlotVersionResponse)
def adopt_revision(revision_id: str, request: PlotVersionRequest):
    try:
        return switch_version(revision_id, request)
    except (ValueError, KeyError, sqlite3.Error, YAMLError) as exc:
        raise _error(exc) from exc


@router.post("/revisions/{revision_id}/rollback", response_model=PlotVersionResponse)
def rollback_revision(revision_id: str, request: PlotVersionRequest):
    try:
        return switch_version(revision_id, request, rollback=True)
    except (ValueError, KeyError, sqlite3.Error, YAMLError) as exc:
        raise _error(exc) from exc
