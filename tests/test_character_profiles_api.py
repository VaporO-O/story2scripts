from fastapi.testclient import TestClient

from story2script.main import app


client = TestClient(app)


def test_character_profiles_api() -> None:
    response = client.post(
        "/api/characters/profiles",
        json={
            "novel_text": (
                "第1章 失踪\n"
                "林澈是林晚的弟弟。林澈敏感、固执、观察力强。"
                "林澈想要找到姐姐失踪真相。林澈说：“我会查下去。”\n"
                "第2章 线索\n林澈继续调查。\n"
                "第3章 追问\n林澈从逃避到主动调查。"
            )
        },
    )

    assert response.status_code == 200
    profile = response.json()["profiles"][0]
    assert profile["name"] == "林澈"
    assert profile["role"] == "主角"
    assert profile["relationships"] == ["是林晚的弟弟"]


def test_character_profiles_api_rejects_short_novel() -> None:
    response = client.post(
        "/api/characters/profiles",
        json={"novel_text": "第1章 开始\n内容一\n第2章 结束\n内容二"},
    )

    assert response.status_code == 422
