from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .parser import parse_chapters
from .screenplay import screenplay_json_schema


class ChapterPreviewRequest(BaseModel):
    novel_text: str = Field(min_length=1)


class ChapterPreviewItem(BaseModel):
    index: int
    title: str
    character_count: int
    preview: str


class ChapterPreviewResponse(BaseModel):
    chapter_count: int
    chapters: list[ChapterPreviewItem]


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
