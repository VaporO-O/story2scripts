from fastapi.testclient import TestClient

from story2script.main import app


client = TestClient(app)


def test_global_state_api_returns_cross_chapter_state_table() -> None:
    response = client.post(
        "/api/consistency/global-state",
        json={
            "novel_text": (
                "第一章 雾起\n清晨，林夏在码头等待。林夏说：“我会查下去。”\n"
                "第二章 旧楼\n林夏来到旧钟楼。\n"
                "第三章 潮汐\n夜里，林夏回到码头。"
            )
        },
    )

    assert response.status_code == 200
    body = response.json()["global_state"]

    assert body["characters"][0]["name"] == "林夏"
    assert body["characters"][0]["id"] == "character-1"
    assert body["locations"][0]["id"].startswith("location-")
    assert body["timeline"][0]["chapter"] == "第一章 雾起"


def test_global_state_api_rejects_less_than_three_chapters() -> None:
    response = client.post(
        "/api/consistency/global-state",
        json={"novel_text": "第一章 开始\n内容一\n第二章 结束\n内容二"},
    )

    assert response.status_code == 422
    assert "3 个章节" in response.json()["detail"]
