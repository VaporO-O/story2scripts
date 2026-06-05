from fastapi.testclient import TestClient

from story2script.main import app


client = TestClient(app)


def test_preview_chapters_api() -> None:
    response = client.post(
        "/api/chapters/preview",
        json={
            "novel_text": "第一章 开始\n内容一\n第二章 转折\n内容二\n第三章 结局\n内容三",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chapter_count"] == 3
    assert body["chapters"][0]["index"] == 1
    assert body["chapters"][0]["title"] == "第一章 开始"


def test_preview_chapters_api_rejects_short_text() -> None:
    response = client.post(
        "/api/chapters/preview",
        json={"novel_text": "第一章 开始\n内容一\n第二章 结束\n内容二"},
    )

    assert response.status_code == 422
    assert "3 个章节" in response.json()["detail"]
