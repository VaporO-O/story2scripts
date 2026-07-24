from fastapi.testclient import TestClient

import time

from story2script.converter import DemoConverter
from story2script.main import app
from story2script.parser import parse_chapters
from story2script.yaml_export import screenplay_from_yaml
from story2script.yaml_export import screenplay_to_yaml


client = TestClient(app)


def sample_yaml_text() -> str:
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )
    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")
    return screenplay_to_yaml(screenplay)


def test_scene_review_api_returns_report() -> None:
    response = client.post(
        "/api/scenes/review",
        json={"yaml_text": sample_yaml_text(), "mode": "demo", "threshold": 7.0},
    )

    assert response.status_code == 200
    body = response.json()
    report = body["report"]

    assert body["screenplay"] is None
    assert body["yaml_text"] is None
    assert "机审完成" in body["message"]
    assert report["threshold"] == 7.0
    assert report["summary"]["scene_count"] == len(report["machine"])
    for scene_id, result in report["machine"].items():
        assert result["scene_id"] == scene_id
        assert result["verdict"] in ("pass", "fail")


def test_scene_review_api_auto_fix_returns_updated_screenplay() -> None:
    response = client.post(
        "/api/scenes/review",
        json={
            "yaml_text": sample_yaml_text(),
            "mode": "demo",
            "auto_fix": True,
            "threshold": 9.5,
            "max_rounds": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["report"]["rounds_used"] <= 2
    assert body["screenplay"] is not None
    # 自动修正后的 YAML 仍能通过 Schema 校验。
    restored = screenplay_from_yaml(body["yaml_text"])
    assert len(restored.scenes) == body["report"]["summary"]["scene_count"]


def test_scene_review_api_supports_scene_subset() -> None:
    response = client.post(
        "/api/scenes/review",
        json={"yaml_text": sample_yaml_text(), "mode": "demo", "scene_ids": ["scene-1"]},
    )

    assert response.status_code == 200
    assert list(response.json()["report"]["machine"].keys()) == ["scene-1"]


def test_scene_review_api_rejects_bad_yaml() -> None:
    response = client.post(
        "/api/scenes/review",
        json={"yaml_text": "not: [valid screenplay", "mode": "demo"},
    )

    assert response.status_code == 422
    assert "YAML 校验失败" in response.json()["detail"]


def test_scene_review_api_rejects_unknown_scene() -> None:
    response = client.post(
        "/api/scenes/review",
        json={"yaml_text": sample_yaml_text(), "mode": "demo", "scene_ids": ["scene-99"]},
    )

    assert response.status_code == 422
    assert "scene-99" in response.json()["detail"]


def test_review_report_merge_api_merges_human_verdicts() -> None:
    report = client.post(
        "/api/scenes/review",
        json={"yaml_text": sample_yaml_text(), "mode": "demo"},
    ).json()["report"]

    response = client.post(
        "/api/review/report/merge",
        json={
            "report": report,
            "verdicts": [
                {"scene_id": "scene-1", "status": "approved", "comment": "节奏可以"},
            ],
        },
    )

    assert response.status_code == 200
    merged = response.json()["report"]
    assert merged["human"]["scene-1"]["status"] == "approved"
    assert merged["summary"]["human_approved"] == 1


def test_review_report_merge_api_rejects_unknown_scene() -> None:
    report = client.post(
        "/api/scenes/review",
        json={"yaml_text": sample_yaml_text(), "mode": "demo"},
    ).json()["report"]

    response = client.post(
        "/api/review/report/merge",
        json={
            "report": report,
            "verdicts": [{"scene_id": "scene-99", "status": "approved"}],
        },
    )

    assert response.status_code == 422
    assert "scene-99" in response.json()["detail"]


def test_convert_api_with_enable_review_attaches_report() -> None:
    response = client.post(
        "/api/convert",
        json={
            "novel_text": (
                "第一章 开始\n林夏说：“出发吧。”\n"
                "第二章 转折\n雨落下来。\n"
                "第三章 结局\n太阳升起。"
            ),
            "title": "测试故事",
            "mode": "demo",
            "enable_review": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["review_report"] is not None
    assert set(body["review_report"]["machine"]) == {
        scene["id"] for scene in body["screenplay"]["scenes"]
    }


def test_convert_job_with_enable_review_attaches_report() -> None:
    start = client.post(
        "/api/convert/jobs",
        json={
            "novel_text": (
                "第一章 开始\n林夏说：“出发吧。”\n"
                "第二章 转折\n雨落下来。\n"
                "第三章 结局\n太阳升起。"
            ),
            "title": "测试故事",
            "mode": "demo",
            "enable_review": True,
        },
    )
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    snapshot = None
    for _ in range(200):
        snapshot = client.get(f"/api/convert/jobs/{job_id}").json()
        if snapshot["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    assert snapshot is not None
    assert snapshot["status"] == "succeeded"
    assert snapshot["result"]["review_report"] is not None
    assert snapshot["result"]["review_report"]["summary"]["scene_count"] > 0
