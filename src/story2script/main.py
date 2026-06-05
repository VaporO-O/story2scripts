from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .api_models import ChapterPreviewItem
from .api_models import ChapterPreviewRequest
from .api_models import ChapterPreviewResponse
from .api_models import ConvertRequest
from .api_models import ConvertResponse
from .api_models import ExampleNovelResponse
from .api_models import ValidateYamlRequest
from .api_models import ValidateYamlResponse
from .converter import DemoConverter
from .examples import load_example_novel
from .parser import parse_chapters
from .screenplay import screenplay_json_schema
from .yaml_export import screenplay_from_yaml
from .yaml_export import screenplay_to_yaml


app = FastAPI(
    title="Story2Script API",
    version="0.1.0",
    description="AI-assisted novel-to-screenplay workbench.",
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    return """
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Story2Script</title>
      </head>
      <body>
        <main>
          <h1>Story2Script</h1>
          <p>AI 辅助小说转剧本工具正在持续开发中。</p>
          <a href="/docs">查看 API 文档</a>
        </main>
      </body>
    </html>
    """


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

    screenplay = DemoConverter().convert(
        chapters=chapters,
        title=request.title,
        genre=request.genre,
    )
    return ConvertResponse(screenplay=screenplay, yaml_text=screenplay_to_yaml(screenplay), mode="demo")


@app.post("/api/yaml/validate", response_model=ValidateYamlResponse)
async def validate_yaml(request: ValidateYamlRequest) -> ValidateYamlResponse:
    try:
        screenplay_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"YAML 校验失败：{exc}") from exc

    return ValidateYamlResponse(valid=True, message="YAML 符合 Story2Script 剧本 Schema。")
