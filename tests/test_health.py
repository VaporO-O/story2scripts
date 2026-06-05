from fastapi.testclient import TestClient

from story2script.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Story2Script"}


def test_index_page() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Story2Script" in response.text
    assert "人物小传" in response.text


def test_static_assets_are_served() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "convertNovel" in response.text
    assert "analyzeCharacters" in response.text
