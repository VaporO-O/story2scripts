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
from .api_models import ExampleNovelResponse
from .api_models import ValidateYamlRequest
from .api_models import ValidateYamlResponse
from .character_profiles import extract_character_profiles
from .converter import get_converter
from .examples import load_example_novel
from .parser import parse_chapters
from .screenplay import screenplay_json_schema
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

    return CharacterProfileResponse(profiles=extract_character_profiles(chapters))


@app.get("/api/screenplay/schema")
async def get_screenplay_schema() -> dict:
    return screenplay_json_schema()


@app.get("/api/examples/novel", response_model=ExampleNovelResponse)
async def get_example_novel() -> ExampleNovelResponse:
    return ExampleNovelResponse(**load_example_novel())


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
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ConvertResponse(
        screenplay=screenplay,
        yaml_text=screenplay_to_yaml(screenplay),
        mode=converter.mode,
    )


@app.post("/api/yaml/validate", response_model=ValidateYamlResponse)
async def validate_yaml(request: ValidateYamlRequest) -> ValidateYamlResponse:
    try:
        screenplay_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"YAML 校验失败：{exc}") from exc

    return ValidateYamlResponse(valid=True, message="YAML 符合 Story2Script 剧本 Schema。")
