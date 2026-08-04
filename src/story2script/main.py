import json
import os
from hmac import compare_digest
from pathlib import Path
from queue import Empty
from time import perf_counter

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
from .api_models import ProviderListResponse
from .api_models import ProviderNameRequest
from .api_models import ProviderSaveRequest
from .api_models import ProviderTestResponse
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
from . import provider_config
from .llm_client import LLMClient
from .scene_chat import parse_rewrite_intent
from .security import (
    API_TOKEN_ENV,
    redact_secrets,
    screen_chat_message,
    screen_novel_text,
)
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
class RevalidatingStaticFiles(StaticFiles):
    """静态资源强制回源校验。

    Starlette 默认只发 ETag / Last-Modified，不发 Cache-Control。缺了它，浏览器会
    对子资源（<script src> / <link href>）启发式缓存、不回源，于是出现「新
    index.html + 旧 app.js/styles.css」这种最难排查的中间态：新加的标签能看见，
    但点击没反应（旧 JS 里没有那个监听），CSS 修复也不生效。

    no-cache 是「先校验再用」而不是「不许存」：ETag 仍在，未改动就返回 304，
    代价只有一个空响应。本项目是本地工作台，正确性远比省这点带宽重要。
    （生产 CDN 场景应改用文件名指纹 + 长缓存，而不是每次校验。）
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")

_PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json"}

# 连通性探测只发一个极短提示词，不该按业务超时（默认 120s）等：配置写错时
# 用户要的是立刻知道，而不是干等两分钟。
_PROVIDER_TEST_TIMEOUT = 20.0


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
    # 与 /static/* 同口径：index.html 是入口，它一旦被缓存，里面引用的
    # app.js / styles.css 版本也跟着被钉死。
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
    )


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


def _sse_frame(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# 队列是线程 Queue（事件由工作线程推送），阻塞 get 会占住事件循环：改为
# 短轮询 + 让出。这两个常量只影响本端点的响应粒度，与业务无关。
_SSE_POLL_SECONDS = 0.2
_SSE_KEEPALIVE_SECONDS = 15.0
_TERMINAL_STATUSES = ("succeeded", "failed")


@app.get("/api/convert/jobs/{job_id}/events")
async def stream_convert_job_events(job_id: str, request: Request) -> StreamingResponse:
    """SSE 事件流：转换过程中逐场景推送，让剧本边生成边显示。

    没有引入 sse-starlette——它只是 mcp 的传递依赖（pyproject 未声明），用了
    就成了隐式依赖。StreamingResponse + anyio 已经够用。
    前端仍保留 1Hz 轮询作为回退：本端点连不上时不影响转换本身。
    """
    if not conversion_jobs.has_job(job_id):
        raise HTTPException(status_code=404, detail="转换任务不存在。")

    async def event_stream():
        queue, backlog = conversion_jobs.subscribe(job_id)
        try:
            finished = False
            # 补发订阅之前已发生的事件：重连不丢场景。
            for event in backlog:
                yield _sse_frame(event)
                if event.get("type") == "done":
                    finished = True

            idle = 0.0
            while not finished:
                if await request.is_disconnected():
                    break
                try:
                    event = queue.get_nowait()
                except Empty:
                    # 任务可能在订阅之前就结束了（缓存命中时转换近乎瞬间完成，
                    # 事件镜像也已随之释放）：补一条终态事件收尾，而不是空转。
                    if conversion_jobs.snapshot(job_id).status in _TERMINAL_STATUSES:
                        # 终态确立后不会再有新的 publish，但这两步之间可能刚好
                        # 到过事件：再排空一次，避免丢掉最后几场。
                        while True:
                            try:
                                yield _sse_frame(queue.get_nowait())
                            except Empty:
                                break
                        snapshot = conversion_jobs.snapshot(job_id)
                        yield _sse_frame(
                            {
                                "type": "done",
                                "status": snapshot.status,
                                "progress": snapshot.progress,
                                "stage": snapshot.stage,
                                "message": snapshot.message,
                            }
                        )
                        break
                    await anyio.sleep(_SSE_POLL_SECONDS)
                    idle += _SSE_POLL_SECONDS
                    if idle >= _SSE_KEEPALIVE_SECONDS:
                        # 注释行（以 : 开头）不触发前端事件，只用于保活。
                        idle = 0.0
                        yield ": keepalive\n\n"
                    continue
                idle = 0.0
                yield _sse_frame(event)
                if event.get("type") == "done":
                    break
        finally:
            # 断开时必须移除订阅者，否则关掉的标签页会留下一个持续增长的队列。
            conversion_jobs.unsubscribe(job_id, queue)
            conversion_jobs.discard_events_if_idle(job_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 防反向代理缓冲，否则事件会被攒成一批、流式效果消失。
            "X-Accel-Buffering": "no",
        },
    )


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


@app.get("/api/providers", response_model=ProviderListResponse)
async def list_provider_profiles() -> ProviderListResponse:
    """列出所有 API 配置。密钥只返回遮罩值。"""
    return ProviderListResponse(**provider_config.list_profiles())


@app.post("/api/providers", response_model=ProviderListResponse)
async def save_provider_profile(request: ProviderSaveRequest) -> ProviderListResponse:
    try:
        data = provider_config.save_profile(
            request.name, request.fields, activate=request.activate
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProviderListResponse(**data)


@app.post("/api/providers/activate", response_model=ProviderListResponse)
async def activate_provider_profile(request: ProviderNameRequest) -> ProviderListResponse:
    """切换生效配置。

    无需重启：项目里没有模块级 LLMClient 单例，`get_converter()` 每次新建实例、
    `LLMClient` 每次重读 .env，所以下一个请求就用新配置。已在跑的任务保持旧配置
    （不该中途换供应商）。
    """
    try:
        data = provider_config.activate_profile(request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"配置不存在：{request.name}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProviderListResponse(**data)


@app.post("/api/providers/delete", response_model=ProviderListResponse)
async def delete_provider_profile(request: ProviderNameRequest) -> ProviderListResponse:
    try:
        data = provider_config.delete_profile(request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"配置不存在：{request.name}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProviderListResponse(**data)


@app.post("/api/providers/test", response_model=ProviderTestResponse)
async def test_provider_profile(request: ProviderNameRequest) -> ProviderTestResponse:
    """对指定配置做一次真实的最小调用，验证 base_url / key / model 是否可用。

    不改变当前生效配置：用该套配置单独建一个 LLMClient，切换前就能先验证。
    """
    try:
        fields = provider_config.profile_secret(request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"配置不存在：{request.name}") from exc

    def _probe() -> tuple[bool, str, str, int]:
        started = perf_counter()
        # overrides 优先级高于进程环境：否则 shell 里残留的 AI_MODEL 会让
        # "测试配置 B" 实际测成当前生效的那一套。load_dotenv=False 同理。
        llm = LLMClient(
            client=httpx.Client(timeout=_PROVIDER_TEST_TIMEOUT),
            usage_label="AI provider test",
            load_dotenv=False,
            overrides=fields,
        )
        try:
            # 连通性探测：绕过缓存，否则第二次测试拿到的是上次的结果。
            llm.complete_json('只返回 {"ok": true}', use_cache=False)
        # 探测失败是预期结果之一（配置写错就是要看到失败），不该让端点 500，
        # 所以这里刻意捕获得比 ValueError 更宽。
        except Exception as exc:
            return False, redact_secrets(str(exc)), fields.get("AI_MODEL", ""), int(
                (perf_counter() - started) * 1000
            )
        finally:
            llm.client.close()
        return (
            True,
            "连接成功，模型有响应。",
            fields.get("AI_MODEL", ""),
            int((perf_counter() - started) * 1000),
        )

    ok, message, model, duration_ms = await anyio.to_thread.run_sync(_probe)
    return ProviderTestResponse(ok=ok, message=message, model=model, duration_ms=duration_ms)


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
