from fastapi import FastAPI
from fastapi.responses import HTMLResponse


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

