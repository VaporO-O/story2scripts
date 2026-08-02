import os
from hmac import compare_digest
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api_models import ChapterPreviewItem
from .api_models import ChapterPreviewRequest
from .api_models import ChapterPreviewResponse
from .api_models import AgentJobStartResponse
from .api_models import AgentJobStatusResponse
from .api_models import AgentRunRequest
from .api_models import AgentSessionDetailResponse
from .api_models import AgentSessionListResponse
from .api_models import CharacterProfileRequest
from .api_models import CharacterProfileResponse
from .api_models import ConvertRequest
from .api_models import ConvertResponse
from .api_models import ConvertJobStartResponse
from .api_models import ConvertJobStatusResponse
from .api_models import ExampleNovelResponse
from .api_models import GlobalStateRequest
from .api_models import JobListResponse
from .api_models import GlobalStateResponse
from .api_models import MetricsEventsResponse
from .api_models import MetricsSummaryResponse
from .api_models import NovelImportRequest
from .api_models import NovelImportResponse
from .api_models import ReviewReportMergeRequest
from .api_models import ReviewReportMergeResponse
from .api_models import RagQueryRequest
from .api_models import RagQueryResponse
from .api_models import SceneChatRequest
from .api_models import SceneChatResponse
from .api_models import SceneReviewRequest
from .api_models import SceneReviewResponse
from .api_models import SceneRewriteRequest
from .api_models import SceneRewriteResponse
from .api_models import TeamJobStartResponse
from .api_models import TeamJobStatusResponse
from .api_models import TeamRunRequest
from .api_models import ValidateYamlRequest
from .api_models import ValidateYamlResponse
from .character_profiles_ai import get_character_profiler
from .agent import AgentSessionStore
from .agent_jobs import agent_jobs
from .conversion_jobs import conversion_jobs
from .job_store import list_jobs as list_recent_jobs
from .team_jobs import team_jobs
from .metrics import metrics
from .converter import get_converter
from .examples import load_example_novel
from .novel_import import import_novel_content
from .parser import parse_chapters
from .rag import build_story_knowledge
from .scene_review import merge_human_verdicts
from .scene_review import review_and_improve
from .scene_review import review_scenes_report
from .scene_rewrite import rewrite_scene
from .screenplay import screenplay_json_schema
from .scene_chat import parse_rewrite_intent
from .security import API_TOKEN_ENV, screen_chat_message, screen_novel_text
from .story_state import extract_global_story_state
from .yaml_export import screenplay_from_yaml
from .yaml_export import screenplay_to_yaml


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="Story2Script API",
    version="0.1.0",
    description="AI-assisted novel-to-screenplay workbench.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """可选的 API Token 校验。

    STORY2SCRIPT_API_TOKEN 未设置时完全放行（本地工作台默认）；设置后除公开
    路径外的 /api/* 都要求 ``Authorization: Bearer <token>``。Token 每请求读取，
    便于在运行期开关，也便于测试注入。
    """
    token = os.getenv(API_TOKEN_ENV, "").strip()
    path = request.url.path
    if (
        token
        and path.startswith("/api/")
        and path not in _PUBLIC_PATHS
    ):
        header = request.headers.get("authorization", "")
        provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not compare_digest(provided, token):
            return JSONResponse(
                status_code=401, content={"detail": "缺少或无效的 API Token。"}
            )
    return await call_next(request)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "Story2Script"}


@app.post("/api/chapters/preview", response_model=ChapterPreviewResponse)
async def preview_chapters(request: ChapterPreviewRequest) -> ChapterPreviewResponse:
    try:
        chapters = parse_chapters(request.novel_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ChapterPreviewResponse(
        chapter_count=len(chapters),
        chapters=[
            ChapterPreviewItem(
                index=index,
                title=chapter.title,
                character_count=len(chapter.content),
                preview=chapter.content[:80],
            )
            for index, chapter in enumerate(chapters, start=1)
        ],
    )


@app.post("/api/characters/profiles", response_model=CharacterProfileResponse)
async def analyze_character_profiles(request: CharacterProfileRequest) -> CharacterProfileResponse:
    try:
        chapters = parse_chapters(request.novel_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        profiler = get_character_profiler(request.mode)
        profiles = profiler.extract(chapters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return CharacterProfileResponse(profiles=profiles, mode=profiler.mode)


@app.get("/api/screenplay/schema")
async def get_screenplay_schema() -> dict:
    return screenplay_json_schema()


@app.post("/api/consistency/global-state", response_model=GlobalStateResponse)
async def preview_global_state(request: GlobalStateRequest) -> GlobalStateResponse:
    try:
        chapters = parse_chapters(request.novel_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return GlobalStateResponse(global_state=extract_global_story_state(chapters))


@app.get("/api/examples/novel", response_model=ExampleNovelResponse)
async def get_example_novel() -> ExampleNovelResponse:
    return ExampleNovelResponse(**load_example_novel())


@app.post("/api/novels/import", response_model=NovelImportResponse)
async def import_novel(request: NovelImportRequest) -> NovelImportResponse:
    try:
        imported = import_novel_content(request.file_name, request.content_base64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return NovelImportResponse(
        file_name=imported.file_name,
        file_type=imported.file_type,
        title=imported.title,
        novel_text=imported.novel_text,
        character_count=imported.character_count,
    )


@app.post("/api/convert", response_model=ConvertResponse)
async def convert_novel(request: ConvertRequest) -> ConvertResponse:
    started = perf_counter()
    try:
        response = _convert_novel_impl(request)
    except HTTPException as exc:
        metrics.record_task(
            "convert",
            mode=request.mode,
            duration_ms=int((perf_counter() - started) * 1000),
            ok=False,
            error=str(exc.detail),
            extra={"source": "sync", "scene_count": 0},
        )
        raise
    metrics.record_task(
        "convert",
        mode=request.mode,
        duration_ms=int((perf_counter() - started) * 1000),
        ok=True,
        extra={"source": "sync", "scene_count": len(response.screenplay.scenes)},
    )
    return response


def _convert_novel_impl(request: ConvertRequest) -> ConvertResponse:
    security_warnings = screen_novel_text(request.novel_text)
    try:
        chapters = parse_chapters(request.novel_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        converter = get_converter(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        screenplay = converter.convert(
            chapters=chapters,
            title=request.title,
            genre=request.genre,
            adaptation_type=request.adaptation_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    review_report = None
    if request.enable_review:
        try:
            screenplay, review_report = review_and_improve(screenplay, mode=converter.mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"质量审校失败：{exc}") from exc

    return ConvertResponse(
        screenplay=screenplay,
        yaml_text=screenplay_to_yaml(screenplay),
        mode=converter.mode,
        adaptation_type=request.adaptation_type,
        review_report=review_report,
        security_warnings=security_warnings,
        conversion_warnings=list(getattr(converter, "last_run_warnings", [])),
    )


@app.post("/api/convert/jobs", response_model=ConvertJobStartResponse)
async def start_convert_job(request: ConvertRequest) -> ConvertJobStartResponse:
    snapshot = conversion_jobs.create(request)
    return ConvertJobStartResponse(
        job_id=snapshot.job_id,
        status=snapshot.status,
        progress=snapshot.progress,
        stage=snapshot.stage,
        message=snapshot.message,
    )


@app.get("/api/convert/jobs/{job_id}", response_model=ConvertJobStatusResponse)
async def get_convert_job(job_id: str) -> ConvertJobStatusResponse:
    if not conversion_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="转换任务不存在。")
    return conversion_jobs.snapshot(job_id)


@app.post("/api/convert/jobs/{job_id}/cancel", response_model=ConvertJobStatusResponse)
async def cancel_convert_job(job_id: str) -> ConvertJobStatusResponse:
    if not conversion_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="转换任务不存在。")
    try:
        conversion_jobs.cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return conversion_jobs.snapshot(job_id)


@app.get("/api/jobs", response_model=JobListResponse)
async def list_all_jobs(limit: int = 50) -> JobListResponse:
    return JobListResponse(jobs=list_recent_jobs(limit=limit))


@app.post("/api/agent/runs", response_model=AgentJobStartResponse)
async def start_agent_run(request: AgentRunRequest) -> AgentJobStartResponse:
    snapshot = agent_jobs.create(request)
    return AgentJobStartResponse(
        job_id=snapshot.job_id,
        status=snapshot.status,
        progress=snapshot.progress,
        stage=snapshot.stage,
        message=snapshot.message,
    )


@app.get("/api/agent/runs/{job_id}", response_model=AgentJobStatusResponse)
async def get_agent_run(job_id: str) -> AgentJobStatusResponse:
    if not agent_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="Agent 任务不存在。")
    return agent_jobs.snapshot(job_id)


@app.post("/api/agent/runs/{job_id}/cancel", response_model=AgentJobStatusResponse)
async def cancel_agent_run(job_id: str) -> AgentJobStatusResponse:
    if not agent_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="Agent 任务不存在。")
    try:
        agent_jobs.cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return agent_jobs.snapshot(job_id)


@app.get("/api/agent/sessions", response_model=AgentSessionListResponse)
async def list_agent_sessions() -> AgentSessionListResponse:
    return AgentSessionListResponse(sessions=AgentSessionStore().list_sessions())


@app.post("/api/agent/teams/runs", response_model=TeamJobStartResponse)
async def start_team_run(request: TeamRunRequest) -> TeamJobStartResponse:
    snapshot = team_jobs.create(request)
    return TeamJobStartResponse(
        job_id=snapshot.job_id,
        status=snapshot.status,
        progress=snapshot.progress,
        stage=snapshot.stage,
        message=snapshot.message,
    )


@app.get("/api/agent/teams/runs/{job_id}", response_model=TeamJobStatusResponse)
async def get_team_run(job_id: str) -> TeamJobStatusResponse:
    if not team_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="协作任务不存在。")
    return team_jobs.snapshot(job_id)


@app.post("/api/agent/teams/runs/{job_id}/cancel", response_model=TeamJobStatusResponse)
async def cancel_team_run(job_id: str) -> TeamJobStatusResponse:
    if not team_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="协作任务不存在。")
    try:
        team_jobs.cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return team_jobs.snapshot(job_id)


@app.get("/api/agent/teams/sessions", response_model=AgentSessionListResponse)
async def list_team_sessions() -> AgentSessionListResponse:
    return AgentSessionListResponse(sessions=AgentSessionStore().list_team_sessions())


@app.get("/api/metrics", response_model=MetricsSummaryResponse)
async def get_metrics_summary() -> MetricsSummaryResponse:
    return MetricsSummaryResponse(**metrics.summary())


@app.get("/api/metrics/events", response_model=MetricsEventsResponse)
async def get_metrics_events(limit: int = 50) -> MetricsEventsResponse:
    return MetricsEventsResponse(events=metrics.recent_events(limit=limit))


@app.post("/api/rag/query", response_model=RagQueryResponse)
async def query_story_knowledge(request: RagQueryRequest) -> RagQueryResponse:
    try:
        chapters = parse_chapters(request.novel_text)
        knowledge = build_story_knowledge(
            chapters, extract_global_story_state(chapters), mode=request.mode
        )
        hits = knowledge.search(
            request.query, top_k=request.top_k, before_chapter=request.before_chapter
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"RAG 查询失败：{exc}") from exc

    return RagQueryResponse(
        retriever=knowledge.retriever_kind, stats=knowledge.stats(), hits=hits
    )


@app.get("/api/agent/sessions/{session_id}", response_model=AgentSessionDetailResponse)
async def get_agent_session(session_id: str) -> AgentSessionDetailResponse:
    try:
        data = AgentSessionStore().load(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentSessionDetailResponse(
        session_id=data["session_id"],
        saved_at=data["saved_at"],
        goal=data["goal"],
        status=data["status"],
        result=data["result"],
        screenplay=data["screenplay"],
        yaml_text=screenplay_to_yaml(data["screenplay"]),
        report=data["report"],
    )


@app.post("/api/yaml/validate", response_model=ValidateYamlResponse)
async def validate_yaml(request: ValidateYamlRequest) -> ValidateYamlResponse:
    try:
        screenplay = screenplay_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"YAML 校验失败：{exc}") from exc

    return ValidateYamlResponse(
        valid=True,
        message="YAML 符合 Story2Script 剧本 Schema。",
        screenplay=screenplay,
    )


@app.post("/api/scenes/rewrite", response_model=SceneRewriteResponse)
async def rewrite_screenplay_scene(request: SceneRewriteRequest) -> SceneRewriteResponse:
    try:
        screenplay = screenplay_from_yaml(request.yaml_text)
        updated_screenplay, message = rewrite_scene(
            screenplay=screenplay,
            scene_id=request.scene_id,
            operation=request.operation,
            character_id=request.character_id,
            tone=request.tone,
            mode=request.mode,
            feedback=request.feedback,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"局部重写失败：{exc}") from exc

    return SceneRewriteResponse(
        screenplay=updated_screenplay,
        yaml_text=screenplay_to_yaml(updated_screenplay),
        scene_id=request.scene_id,
        operation=request.operation,
        mode=request.mode,
        message=message,
    )


@app.post("/api/scenes/chat", response_model=SceneChatResponse)
async def chat_rewrite_scene(request: SceneChatRequest) -> SceneChatResponse:
    """对话式改写：解析一句自然语言要求，再执行对应的受校验局部重写。"""
    # 这句话会进入意图解析提示词的指令位，按 Agent 目标的口径阻断而非告警。
    try:
        screen_chat_message(request.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        screenplay = screenplay_from_yaml(request.yaml_text)
        intent = parse_rewrite_intent(
            screenplay=screenplay,
            message=request.message,
            history=request.history,
            mode=request.mode,
            current_scene_id=request.scene_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"改写要求解析失败：{exc}") from exc

    if intent.operation is None:
        # 解析不出可执行操作（含"改到白天"这类硬性不可改字段）：只回话，不动剧本。
        return SceneChatResponse(
            reply=intent.reply or intent.refusal,
            mode=request.mode,
            scene_id=intent.scene_id,
            refusal=intent.refusal,
        )

    try:
        updated_screenplay, message = rewrite_scene(
            screenplay=screenplay,
            scene_id=intent.scene_id,
            operation=intent.operation,
            character_id=intent.character_id,
            tone=intent.tone or "更克制",
            mode=request.mode,
            # 用户原话随行：操作定大方向，原话提供枚举表达不了的细微差别。
            feedback=intent.feedback,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"局部重写失败：{exc}") from exc

    return SceneChatResponse(
        reply=f"{intent.reply} {message}".strip(),
        mode=request.mode,
        screenplay=updated_screenplay,
        yaml_text=screenplay_to_yaml(updated_screenplay),
        scene_id=intent.scene_id,
        operation=intent.operation,
    )


@app.post("/api/scenes/review", response_model=SceneReviewResponse)
async def review_screenplay_scenes(request: SceneReviewRequest) -> SceneReviewResponse:
    try:
        screenplay = screenplay_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"YAML 校验失败：{exc}") from exc

    try:
        if request.auto_fix:
            updated, report = review_and_improve(
                screenplay,
                mode=request.mode,
                threshold=request.threshold,
                max_rounds=request.max_rounds,
            )
            return SceneReviewResponse(
                report=report,
                screenplay=updated,
                yaml_text=screenplay_to_yaml(updated),
                mode=request.mode,
                message=(
                    f"机审完成：{report.summary.get('pass_count', 0)} 个场景通过，"
                    f"{report.summary.get('fail_count', 0)} 个未通过，共执行 {report.rounds_used} 轮审校。"
                ),
            )
        report = review_scenes_report(
            screenplay,
            mode=request.mode,
            threshold=request.threshold,
            scene_ids=request.scene_ids or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"机审失败：{exc}") from exc

    return SceneReviewResponse(
        report=report,
        mode=request.mode,
        message=(
            f"机审完成：{report.summary.get('pass_count', 0)} 个场景通过，"
            f"{report.summary.get('fail_count', 0)} 个未通过。"
        ),
    )


@app.post("/api/review/report/merge", response_model=ReviewReportMergeResponse)
async def merge_review_report(request: ReviewReportMergeRequest) -> ReviewReportMergeResponse:
    try:
        merged = merge_human_verdicts(request.report, request.verdicts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReviewReportMergeResponse(report=merged)
