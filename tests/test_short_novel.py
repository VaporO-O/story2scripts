import json

import httpx
import pytest
from fastapi.testclient import TestClient

from story2script.converter import AIConverter, DemoConverter
from story2script.main import app
from story2script.parser import parse_chapters
from story2script.yaml_export import screenplay_from_yaml, screenplay_to_yaml
from test_convert_api import wait_for_convert_job


TEXT = "林夏站在车站，打开一封信。林夏说：“我会查清楚。”"


@pytest.mark.parametrize(
    "novel,count",
    [
        (TEXT, 1),
        ("第一章 来信\n" + TEXT, 1),
        ("第一章\n" + TEXT + "\n第二章\n周宁推开仓库的门。", 2),
    ],
)
def test_short_novel_full_pipeline_roundtrip(novel, count):
    client = TestClient(app)
    preview = client.post("/api/chapters/preview", json={"novel_text": novel})
    assert preview.status_code == 200 and preview.json()["chapter_count"] == count
    response = client.post("/api/convert", json={"novel_text": novel})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["screenplay"]["source"]["chapter_count"] == count
    script = screenplay_from_yaml(body["yaml_text"])
    assert screenplay_from_yaml(screenplay_to_yaml(script)) == script
    job = client.post("/api/convert/jobs", json={"novel_text": novel}).json()
    assert wait_for_convert_job(job["job_id"])["status"] == "succeeded"


def test_single_chapter_ai_uses_same_schema_without_dummy_chapters(monkeypatch):
    for key, value in {
        "AI_API_KEY": "test",
        "AI_BASE_URL": "https://example.test/v1",
        "AI_MODEL": "test",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AI_EMBED_MODEL", raising=False)
    chapters = parse_chapters(TEXT)
    demo = DemoConverter().convert(chapters)

    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        result = (
            {"scenes": [s.model_dump(mode="json") for s in demo.scenes]}
            if "本章片段原文" in prompt
            else []
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    script = AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler))).convert(
        chapters
    )
    assert script.source.chapter_count == 1 and script.source.chapter_titles == ["正文"]


def test_one_chapter_search_scope_and_empty_input():
    client = TestClient(app)
    answer = client.post("/api/rag/answer", json={"novel_text": TEXT, "query": "信"})
    assert answer.status_code == 200 and answer.json()["citations"][0]["chapter"] == "正文"
    blocked = client.post(
        "/api/rag/answer", json={"novel_text": TEXT, "query": "信", "before_chapter": 1}
    )
    assert blocked.status_code == 200 and blocked.json()["abstained"]
    assert (
        client.post(
            "/api/rag/query", json={"novel_text": TEXT, "query": "信", "before_chapter": 3}
        ).status_code
        == 422
    )
    assert client.post("/api/convert", json={"novel_text": " \n "}).status_code == 422


@pytest.mark.anyio
async def test_mcp_accepts_unheaded_short_story():
    from story2script.mcp_server import convert_novel, workspace

    result = await convert_novel(novel_text=TEXT, mode="demo")
    stored = workspace.get_screenplay(result["screenplay_id"])
    assert stored.screenplay.source.chapter_count == 1
