from fastapi.testclient import TestClient

from story2script.converter import DemoConverter
from story2script.main import app
from story2script.parser import parse_chapters
from story2script.yaml_export import screenplay_to_yaml


client = TestClient(app)


def valid_yaml_text() -> str:
    chapters = parse_chapters("第一章 A\n林夏说：“走吧。”\n第二章 B\n内容二\n第三章 C\n内容三")
    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")
    return screenplay_to_yaml(screenplay)


def test_validate_yaml_api_accepts_valid_yaml() -> None:
    response = client.post("/api/yaml/validate", json={"yaml_text": valid_yaml_text()})
    body = response.json()

    assert response.status_code == 200
    assert body["valid"] is True
    assert body["message"] == "YAML 符合 Story2Script 剧本 Schema。"
    # 校验成功时一并返回解析后的剧本，供前端刷新分场预览。
    assert body["screenplay"]["title"] == "测试故事"
    assert len(body["screenplay"]["scenes"]) >= 1


def test_validate_yaml_api_rejects_invalid_yaml() -> None:
    response = client.post("/api/yaml/validate", json={"yaml_text": "title: [broken"})

    assert response.status_code == 422
    assert "YAML 校验失败" in response.json()["detail"]


def test_validate_yaml_api_rejects_schema_violation() -> None:
    yaml_text = valid_yaml_text().replace("chapter_count: 3", "chapter_count: 2")

    response = client.post("/api/yaml/validate", json={"yaml_text": yaml_text})

    assert response.status_code == 422
    assert "YAML 校验失败" in response.json()["detail"]
