import time

from fastapi.testclient import TestClient

import story2script.main as main_module
from story2script.main import app


client = TestClient(app)


def wait_for_convert_job(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/convert/jobs/{job_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] in {"succeeded", "failed"}:
            return latest
        time.sleep(0.05)
    return latest


def test_convert_api_returns_demo_screenplay() -> None:
    response = client.post(
        "/api/convert",
        json={
            "title": "测试故事",
            "genre": "剧情",
            "adaptation_type": "分镜脚本",
            "novel_text": (
                "第一章 开始\n林夏说：“出发吧。”\n"
                "第二章 转折\n雨落下来。\n"
                "第三章 结局\n太阳升起。"
            ),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "demo"
    assert body["adaptation_type"] == "分镜脚本"
    assert body["screenplay"]["title"] == "测试故事"
    assert body["screenplay"]["adaptation_type"] == "分镜脚本"
    assert body["yaml_text"].startswith("schema_version: '1.0'")
    assert "adaptation_type: 分镜脚本" in body["yaml_text"]
    assert "global_state:" in body["yaml_text"]
    assert body["screenplay"]["source"]["chapter_count"] == 3
    assert len(body["screenplay"]["global_state"]["timeline"]) == 3
    assert body["screenplay"]["scenes"][0]["int_ext"] in ["INT.", "EXT."]
    assert body["screenplay"]["scenes"][0]["time_of_day"] in ["DAY", "NIGHT"]
    assert "characters_present" in body["screenplay"]["scenes"][0]
    assert "props" in body["screenplay"]["scenes"][0]
    assert "dramatization_decisions" in body["screenplay"]["scenes"][0]
    assert len(body["screenplay"]["scenes"]) == 3


def test_convert_api_rejects_less_than_three_chapters() -> None:
    response = client.post(
        "/api/convert",
        json={
            "novel_text": "第一章 开始\n内容一\n第二章 结束\n内容二",
        },
    )

    assert response.status_code == 422
    assert "3 个章节" in response.json()["detail"]


def test_convert_api_rejects_unknown_adaptation_type() -> None:
    response = client.post(
        "/api/convert",
        json={
            "adaptation_type": "小说复述",
            "novel_text": (
                "第一章 开始\n内容一\n"
                "第二章 转折\n内容二\n"
                "第三章 结局\n内容三"
            ),
        },
    )

    assert response.status_code == 422


def test_convert_api_does_not_return_yaml_when_ai_validation_fails(monkeypatch) -> None:
    class FailingConverter:
        mode = "ai"

        def convert(self, **kwargs):
            raise ValueError("AI 全文转换失败：模型返回结果不符合 Screenplay Schema。")

    monkeypatch.setattr(main_module, "get_converter", lambda mode: FailingConverter())

    response = client.post(
        "/api/convert",
        json={
            "mode": "ai",
            "novel_text": (
                "第一章 开始\n内容一\n"
                "第二章 转折\n内容二\n"
                "第三章 结局\n内容三"
            ),
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert "AI 全文转换失败" in body["detail"]
    assert "yaml_text" not in body


def test_convert_job_api_reports_progress_and_result() -> None:
    response = client.post(
        "/api/convert/jobs",
        json={
            "title": "任务故事",
            "genre": "剧情",
            "adaptation_type": "影视剧",
            "mode": "demo",
            "novel_text": (
                "第一章 开始\n林夏说：“出发吧。”\n"
                "第二章 转折\n雨落下来。\n"
                "第三章 结局\n太阳升起。"
            ),
        },
    )

    assert response.status_code == 200
    started = response.json()
    assert started["status"] in {"queued", "running", "succeeded"}
    assert started["progress"] >= 0
    assert started["stage"]

    completed = wait_for_convert_job(started["job_id"])

    assert completed["status"] == "succeeded"
    assert completed["progress"] == 100
    assert completed["stage"] == "转换完成"
    assert completed["result"]["mode"] == "demo"
    assert completed["result"]["screenplay"]["title"] == "任务故事"
    assert completed["result"]["yaml_text"].startswith("schema_version: '1.0'")


def test_convert_job_api_reports_failure() -> None:
    response = client.post(
        "/api/convert/jobs",
        json={
            "mode": "demo",
            "novel_text": "第一章 开始\n内容一\n第二章 结束\n内容二",
        },
    )

    assert response.status_code == 200
    completed = wait_for_convert_job(response.json()["job_id"])

    assert completed["status"] == "failed"
    assert completed["stage"] == "转换失败"
    assert "3 个章节" in completed["error"]
    assert completed["result"] is None


def test_convert_job_api_rejects_missing_job() -> None:
    response = client.get("/api/convert/jobs/missing-job")

    assert response.status_code == 404
    assert "转换任务不存在" in response.json()["detail"]

