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
        "adaptation_type": "影视剧",
        "logline": "一名记者追查父亲失踪真相。",
        "source": {
            "chapter_count": 3,
            "chapter_titles": ["第一章", "第二章", "第三章"],
        },
        "global_state": {
            "characters": [
                {
                    "id": "character-1",
                    "name": "林夏",
                    "aliases": [],
                    "first_appearance": "第一章",
                    "appearance_chapters": ["第一章", "第二章", "第三章"],
                    "traits": ["冷静"],
                    "goal": "寻找真相",
                    "arc": "从被动接收线索到主动追查。",
                    "consistency_note": "后续分块转换必须保持姓名、性格、目标和人物弧光一致。",
                }
            ],
            "locations": [
                {
                    "id": "location-1",
                    "name": "雾港码头",
                    "first_appearance": "第一章",
                    "appearance_chapters": ["第一章"],
                    "description": "雾气笼罩码头。",
                }
            ],
            "timeline": [
                {
                    "id": "event-1",
                    "order": 1,
                    "chapter": "第一章",
                    "time_marker": "",
                    "summary": "林夏发现匿名信。",
                }
            ],
        },
        "characters": [
            {
                "id": "character-1",
                "name": "林夏",
                "description": "年轻记者",
                "motivation": "寻找真相",
                "arc": "从被动接收线索到主动追查。",
            }
        ],
        "scenes": [
            {
                "id": "scene-1",
                "heading": "EXT. 雾港码头 - DAY",
                "int_ext": "EXT.",
                "time_of_day": "DAY",
                "location": "雾港码头",
                "source_chapter": "第一章",
                "summary": "林夏发现匿名信。",
                "goal": "林夏想确认匿名信来源。",
                "conflict": "匿名信缺少署名，阻碍林夏判断真相。",
                "beat": "线索出现",
                "subtext": "林夏表面冷静，实际已经开始怀疑旧案。",
                "characters": ["character-1"],
                "characters_present": ["character-1"],
                "props": ["匿名信"],
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


def test_screenplay_model_rejects_unknown_character_present_reference() -> None:
    data = sample_screenplay()
    data["scenes"][0]["characters_present"] = ["missing-character"]

    with pytest.raises(ValidationError, match="不存在的角色"):
        Screenplay.model_validate(data)


def test_screenplay_model_rejects_source_count_mismatch() -> None:
    data = sample_screenplay()
    data["source"]["chapter_count"] = 4

    with pytest.raises(ValidationError, match="chapter_count"):
        Screenplay.model_validate(data)


def test_screenplay_model_requires_dramatic_scene_fields() -> None:
    data = sample_screenplay()
    del data["scenes"][0]["conflict"]

    with pytest.raises(ValidationError, match="conflict"):
        Screenplay.model_validate(data)


def test_screenplay_model_requires_production_scene_fields() -> None:
    data = sample_screenplay()
    del data["scenes"][0]["int_ext"]

    with pytest.raises(ValidationError, match="int_ext"):
        Screenplay.model_validate(data)


def test_screenplay_model_rejects_heading_mismatch_with_production_fields() -> None:
    data = sample_screenplay()
    data["scenes"][0]["heading"] = "INT. 雾港码头 - DAY"

    with pytest.raises(ValidationError, match="heading"):
        Screenplay.model_validate(data)


def test_screenplay_model_rejects_unknown_global_state_character() -> None:
    data = sample_screenplay()
    data["global_state"]["characters"][0]["id"] = "character-99"

    with pytest.raises(ValidationError, match="全局状态表引用"):
        Screenplay.model_validate(data)


def test_screenplay_schema_endpoint() -> None:
    response = client.get("/api/screenplay/schema")

    assert response.status_code == 200
    assert response.json()["title"] == "Story2Script Screenplay"
    assert "scenes" in response.json()["properties"]
    assert response.json()["properties"]["adaptation_type"]["enum"] == [
        "短剧",
        "影视剧",
        "舞台剧",
        "广播剧",
        "分镜脚本",
    ]
    assert "global_state" in response.json()["required"]
    assert "timeline" in response.json()["properties"]["global_state"]["required"]
    scene_schema = response.json()["properties"]["scenes"]["items"]
    assert "conflict" in scene_schema["required"]
    assert "int_ext" in scene_schema["required"]
    assert scene_schema["properties"]["int_ext"]["enum"] == ["INT.", "EXT."]
    assert scene_schema["properties"]["time_of_day"]["enum"] == ["DAY", "NIGHT"]


def test_schema_file_is_valid_json() -> None:
    schema_path = Path(__file__).parents[1] / "schema" / "screenplay.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["title"] == "Story2Script Screenplay"
    assert "adaptation_type" in schema["required"]
    assert "global_state" in schema["required"]
    assert schema["properties"]["source"]["properties"]["chapter_count"]["minimum"] == 3
    assert "arc" in schema["properties"]["characters"]["items"]["required"]
    scene_schema = schema["properties"]["scenes"]["items"]
    assert "characters_present" in scene_schema["required"]
    assert "props" in scene_schema["required"]
