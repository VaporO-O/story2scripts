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
    assert "adaptationTypeInput" in response.text
    assert "分镜脚本" in response.text
    # 六个固定重写按钮已被对话式改写取代：这里断言改写入口本身。
    # 三个操作文案并未消失，改为"我理解为…"的标签，断言移到 app.js
    # （见 test_static_assets_are_served）。
    assert "chatInput" in response.text
    assert "chatModeInput" in response.text


def test_static_assets_are_served() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "convertNovel" in response.text
    assert "analyzeCharacters" in response.text
    # 六种操作的中文文案从 HTML 按钮迁到这里：解析结果以"我理解为：加强戏剧冲突"呈现
    assert "重新生成本场对白" in response.text
    assert "加强戏剧冲突" in response.text
    assert "减少旁白" in response.text
