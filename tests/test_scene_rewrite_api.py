from fastapi.testclient import TestClient

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


def test_scene_rewrite_api_returns_updated_yaml() -> None:
    response = client.post(
        "/api/scenes/rewrite",
        json={
            "yaml_text": sample_yaml_text(),
            "scene_id": "scene-1",
            "operation": "add_camera_hints",
        },
    )

    assert response.status_code == 200
    body = response.json()
    restored = screenplay_from_yaml(body["yaml_text"])

    assert body["scene_id"] == "scene-1"
    assert body["operation"] == "add_camera_hints"
    assert "镜头提示" in "\n".join(restored.scenes[0].camera_hints)


def test_scene_rewrite_api_rejects_missing_scene() -> None:
    response = client.post(
        "/api/scenes/rewrite",
        json={
            "yaml_text": sample_yaml_text(),
            "scene_id": "missing-scene",
            "operation": "add_camera_hints",
        },
    )

    assert response.status_code == 422
    assert "局部重写失败" in response.json()["detail"]
