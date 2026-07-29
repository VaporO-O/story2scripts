import time

import pytest
from fastapi.testclient import TestClient

from story2script.converter import DemoConverter
from story2script.main import app
from story2script.parser import parse_chapters
from story2script.yaml_export import screenplay_to_yaml

client = TestClient(app)

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"


def sample_yaml_text() -> str:
    chapters = parse_chapters(NOVEL)
    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")
    return screenplay_to_yaml(screenplay)


def wait_for_agent_job(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/agent/runs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("Agent 任务超时未完成")


def test_agent_run_demo_succeeds():
    response = client.post(
        "/api/agent/runs",
        json={
            "yaml_text": sample_yaml_text(),
            "goal": "全场景达标",
            "mode": "demo",
            "threshold": 9.5,
            "max_steps": 8,
        },
    )
    assert response.status_code == 200
    start = response.json()
    assert start["status"] in {"queued", "running", "succeeded"}

    payload = wait_for_agent_job(start["job_id"])
    assert payload["status"] == "succeeded"
    assert payload["progress"] == 100

    result = payload["result"]
    agent_result = result["result"]
    assert agent_result["status"] in {"completed", "budget_exhausted"}
    assert agent_result["goal"] == "全场景达标"
    assert agent_result["trace"]
    assert agent_result["final_summary"]["avg_score"] >= agent_result["initial_summary"]["avg_score"]
    assert result["yaml_text"].startswith("schema_version:")
    assert result["report"] is not None


def test_agent_run_invalid_yaml_fails():
    response = client.post("/api/agent/runs", json={"yaml_text": "title: 不是剧本"})
    assert response.status_code == 200

    payload = wait_for_agent_job(response.json()["job_id"])
    assert payload["status"] == "failed"
    assert "YAML" in payload["error"]
    assert payload["result"] is None


def test_agent_run_unknown_job_returns_404():
    response = client.get("/api/agent/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent 任务不存在。"


def test_agent_sessions_listing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    empty = client.get("/api/agent/sessions")
    assert empty.status_code == 200
    assert empty.json()["sessions"] == []

    response = client.post(
        "/api/agent/runs",
        json={
            "yaml_text": sample_yaml_text(),
            "goal": "留档",
            "mode": "demo",
            "threshold": 9.5,
            "max_steps": 3,
            "save_session": True,
        },
    )
    payload = wait_for_agent_job(response.json()["job_id"])
    assert payload["status"] == "succeeded"
    session_id = payload["result"]["result"]["session_id"]
    assert session_id.startswith("ag-")

    listing = client.get("/api/agent/sessions")
    sessions = listing.json()["sessions"]
    assert [item["session_id"] for item in sessions] == [session_id]
    assert sessions[0]["goal"] == "留档"
