import pytest
from fastapi.testclient import TestClient

from story2script.converter import AIConverter, DemoConverter, get_converter
from story2script.main import app


client = TestClient(app)


def english_novel() -> str:
    return "Chapter 1 Start\nLin says hello.\nChapter 2 Middle\nRain falls.\nChapter 3 End\nSun rises."


def test_get_converter_returns_demo_converter() -> None:
    converter = get_converter("demo")

    assert isinstance(converter, DemoConverter)
    assert converter.mode == "demo"


def test_get_converter_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported converter mode"):
        get_converter("unknown")


def test_get_converter_returns_ai_converter() -> None:
    converter = get_converter("ai")

    assert isinstance(converter, AIConverter)
    assert converter.mode == "ai"


def test_convert_api_accepts_explicit_demo_mode() -> None:
    response = client.post(
        "/api/convert",
        json={"mode": "demo", "novel_text": english_novel()},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "demo"


def test_convert_api_rejects_unconfigured_ai_mode() -> None:
    response = client.post(
        "/api/convert",
        json={"mode": "ai", "novel_text": english_novel()},
    )

    assert response.status_code == 422
    assert "AI mode requires" in response.json()["detail"]
