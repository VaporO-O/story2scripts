from fastapi.testclient import TestClient

from story2script.main import app


client = TestClient(app)


def test_convert_api_returns_demo_screenplay() -> None:
    response = client.post(
        "/api/convert",
        json={
            "title": "测试故事",
            "genre": "剧情",
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
    assert body["screenplay"]["title"] == "测试故事"
    assert body["screenplay"]["source"]["chapter_count"] == 3
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

