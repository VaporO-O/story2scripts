from fastapi.testclient import TestClient

from story2script.main import app
from story2script.parser import parse_chapters


client = TestClient(app)


def test_example_novel_api_returns_convertible_text() -> None:
    response = client.get("/api/examples/novel")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "雾港来信"
    assert body["genre"] == "悬疑 / 剧情"
    assert len(parse_chapters(body["novel_text"])) == 3


def test_example_novel_can_be_converted() -> None:
    example = client.get("/api/examples/novel").json()

    response = client.post("/api/convert", json=example)

    assert response.status_code == 200
    assert response.json()["screenplay"]["title"] == "雾港来信"
