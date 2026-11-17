"""Authoring APIs: no export or draft save implicitly adopts a plot candidate."""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response, Header
from fastapi.responses import JSONResponse
from yaml import YAMLError

from .creative_models import CreativeBrief
from .creative_templates import list_templates, save_template
from .plot_store import load_record
from .screenplay import Screenplay
from .screenplay_export import script_docx, script_html, script_txt
from .security import redact_secrets
from .studio import apply_edit, projection
from .studio_models import (
    DraftRequest,
    EditRequest,
    EmotionRequest,
    ExportRequest,
    ProjectionRequest,
    RecheckRequest,
    StructureRequest,
)
from .studio_store import DraftConflict, draft_versions, list_drafts, save_draft
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml
from .studio_tasks import _result
from .studio_tasks import (
    propose_structure as propose_structure,
    emotion_analysis as emotion_analysis,
    recheck_script as recheck_script,
)
from .studio_jobs import studio_jobs
from .studio_models import StudioJobRequest, StudioJobStatus
from .job_store import list_jobs

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _error(exc):
    return HTTPException(
        409 if isinstance(exc, DraftConflict) else 404 if isinstance(exc, KeyError) else 422,
        "草稿或版本不存在。" if isinstance(exc, KeyError) else redact_secrets(str(exc)),
    )


@router.get("/templates")
def templates():
    return {"templates": list_templates()}


@router.post("/templates")
def create_template(brief: CreativeBrief):
    return save_template(brief)


@router.post("/projection")
def project_story(request: ProjectionRequest):
    try:
        return projection(
            screenplay_from_yaml(request.yaml_text), request.scene_id, request.boundary
        )
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.post("/edit")
def edit_script(request: EditRequest):
    try:
        return _result(
            apply_edit(
                screenplay_from_yaml(request.yaml_text),
                scene=request.scene,
                structure=request.structure,
                creative_brief=request.creative_brief,
            )
        )
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.get("/drafts")
def drafts():
    return {"drafts": list_drafts()}


@router.post("/drafts")
def store_draft(request: DraftRequest):
    try:
        payload = save_draft(request)
        script = Screenplay.model_validate(payload.pop("screenplay"))
        return _result(script, **{k: v for k, v in payload.items() if k != "hash"})
    except (ValueError, KeyError, YAMLError) as exc:
        raise _error(exc) from exc


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, version: int | None = None):
    try:
        key = draft_id if version is None else f"{draft_id}:{version:08d}"
        payload = load_record("studio_draft" if version is None else "studio_snapshot", key)
        script = Screenplay.model_validate(payload.pop("screenplay"))
        return _result(script, **{k: v for k, v in payload.items() if k != "hash"})
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.get("/drafts/{draft_id}/versions")
def versions(draft_id: str):
    return {"versions": draft_versions(draft_id)}


@router.post("/structure")
def structure(request: StructureRequest, idempotency_key: str = Header(default="")):
    try:
        if request.mode == "ai":
            return queue_legacy("structure", request, idempotency_key)
        return _result(propose_structure(request), method=request.mode)
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.post("/emotions")
def emotions(request: EmotionRequest, idempotency_key: str = Header(default="")):
    try:
        if request.mode == "ai":
            return queue_legacy("emotions", request, idempotency_key)
        return emotion_analysis(request)
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.post("/recheck")
def recheck(request: RecheckRequest, idempotency_key: str = Header(default="")):
    try:
        if request.mode == "ai":
            return queue_legacy("recheck", request, idempotency_key)
        return recheck_script(request)
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


def queue_legacy(operation, request, key):
    job = studio_jobs.create(
        StudioJobRequest(operation=operation, parameters=request.model_dump(mode="json")),
        idempotency_key=key,
    )
    return JSONResponse(status_code=202, content=job.model_dump(mode="json"))


@router.post("/jobs", response_model=StudioJobStatus, status_code=202)
def start_studio_job(request: StudioJobRequest, idempotency_key: str = Header(default="")):
    try:
        return studio_jobs.create(request, idempotency_key=idempotency_key)
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc


@router.get("/jobs")
def studio_history(limit: int = 30):
    return {"jobs": list_jobs(limit=limit, db_path=studio_jobs._db_path(), kind="studio")}


@router.get("/jobs/requests/{key}", response_model=StudioJobStatus)
def studio_by_request(key: str):
    try:
        return studio_jobs.find_request(key)
    except KeyError as exc:
        raise _error(exc) from exc


@router.get("/jobs/{job_id}", response_model=StudioJobStatus)
def studio_status(job_id: str):
    try:
        return studio_jobs.snapshot(job_id)
    except KeyError as exc:
        raise _error(exc) from exc


@router.get("/jobs/{job_id}/input")
def studio_input(job_id: str):
    try:
        request = studio_jobs._request_for(job_id)
        script = screenplay_from_yaml(request.parameters["yaml_text"])
        return {"request": request, "screenplay": script, "yaml_text": screenplay_to_yaml(script)}
    except KeyError as exc:
        raise _error(exc) from exc


@router.post("/jobs/{job_id}/resume", response_model=StudioJobStatus, status_code=202)
def studio_resume(job_id: str):
    try:
        return studio_jobs.resume(job_id)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("/jobs/{job_id}/cancel", response_model=StudioJobStatus)
def studio_cancel(job_id: str):
    try:
        studio_jobs.cancel(job_id)
        return studio_jobs.snapshot(job_id)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("/export")
def export_script(request: ExportRequest):
    try:
        script = screenplay_from_yaml(request.yaml_text)
        if request.format == "txt":
            content, media = (
                script_txt(script, request.include_appendix),
                "text/plain; charset=utf-8",
            )
        elif request.format == "html":
            content, media = (
                script_html(script, request.include_appendix),
                "text/html; charset=utf-8",
            )
        else:
            content, media = (
                script_docx(script, request.include_appendix),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        filename = quote(script.title[:80] + "." + request.format, safe="")
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except (ValueError, YAMLError) as exc:
        raise _error(exc) from exc
