import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from story2script.main import app
from story2script.screenplay import Screenplay


client = TestClient(app)


def sample_screenplay() -> dict:
    return {
        "schema_version": "1.0",
        "title": "雾港来信",
        "genre": "悬疑",
        "logline": "一名记者追查父亲失踪真相。",
        "source": {
            "chapter_count": 3,
            "chapter_titles": ["第一章", "第二章", "第三章"],
        },
        "characters": [
            {
                "id": "character-1",
                "name": "林夏",
                "description": "年轻记者",
                "motivation": "寻找真相",
            }
        ],
        "scenes": [
            {
                "id": "scene-1",
                "heading": "EXT. 雾港码头 - DAWN",
                "source_chapter": "第一章",
                "summary": "林夏发现匿名信。",
                "characters": ["character-1"],
                "elements": [
                    {"type": "action", "text": "雾气笼罩码头。"},
                    {
                        "type": "dialogue",
                        "character": "character-1",
                        "parenthetical": "低声",
                        "text": "这不是巧合。",
                    },
                ],
            }
        ],
    }


def test_screenplay_model_accepts_valid_structure() -> None:
    screenplay = Screenplay.model_validate(sample_screenplay())

    assert screenplay.schema_version == "1.0"
    assert screenplay.source.chapter_count == 3


def test_screenplay_model_rejects_unknown_character_reference() -> None:
    data = sample_screenplay()
    data["scenes"][0]["characters"] = ["missing-character"]

    with pytest.raises(ValidationError, match="不存在的角色"):
        Screenplay.model_validate(data)


def test_screenplay_model_rejects_source_count_mismatch() -> None:
    data = sample_screenplay()
    data["source"]["chapter_count"] = 4

    with pytest.raises(ValidationError, match="chapter_count"):
        Screenplay.model_validate(data)


def test_screenplay_schema_endpoint() -> None:
    response = client.get("/api/screenplay/schema")

    assert response.status_code == 200
    assert response.json()["title"] == "Story2Script Screenplay"
    assert "scenes" in response.json()["properties"]


def test_schema_file_is_valid_json() -> None:
    schema_path = Path(__file__).parents[1] / "schema" / "screenplay.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["title"] == "Story2Script Screenplay"
    assert schema["properties"]["source"]["properties"]["chapter_count"]["minimum"] == 3
