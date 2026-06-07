from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_models import ChapterPreviewItem
from .api_models import ChapterPreviewRequest
from .api_models import ChapterPreviewResponse
from .api_models import CharacterProfileRequest
from .api_models import CharacterProfileResponse
from .api_models import ConvertRequest
from .api_models import ConvertResponse
from .api_models import ConvertJobStartResponse
from .api_models import ConvertJobStatusResponse
from .api_models import ExampleNovelResponse
from .api_models import GlobalStateRequest
from .api_models import GlobalStateResponse
from .api_models import NovelImportRequest
from .api_models import NovelImportResponse
from .api_models import SceneRewriteRequest
from .api_models import SceneRewriteResponse
from .api_models import ValidateYamlRequest
from .api_models import ValidateYamlResponse
from .character_profiles_ai import get_character_profiler
from .conversion_jobs import conversion_jobs
from .converter import get_converter
from .examples import load_example_novel
from .novel_import import import_novel_content
from .parser import parse_chapters
from .scene_rewrite import rewrite_scene
from .screenplay import screenplay_json_schema
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

    return ConvertResponse(
        screenplay=screenplay,
        yaml_text=screenplay_to_yaml(screenplay),
        mode=converter.mode,
        adaptation_type=request.adaptation_type,
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
