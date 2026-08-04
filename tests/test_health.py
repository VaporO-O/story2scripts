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


def test_static_assets_force_revalidation() -> None:
    """静态资源必须带 Cache-Control，否则浏览器会启发式缓存子资源。

    Starlette 默认只发 ETag / Last-Modified。缺了 Cache-Control 时，浏览器对
    <script src> / <link href> 可以不回源直接用旧文件，于是出现「新 index.html
    + 旧 app.js/styles.css」的中间态：新加的导航标签能看见，但点击毫无反应
    （旧 JS 里没有那个监听），CSS 修复同样不生效。这类症状看起来像功能没做，
    极难排查，所以钉死在测试里。
    """
    for path in ("/", "/static/app.js", "/static/styles.css"):
        response = client.get(path)

        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-cache", path


def test_static_assets_still_return_304_when_unchanged() -> None:
    """no-cache 是「先校验再用」而非「不许存」：未改动仍应命中 304，不白传内容。"""
    first = client.get("/static/app.js")
    etag = first.headers["etag"]

    revalidated = client.get("/static/app.js", headers={"If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.content == b""


def test_static_assets_are_served() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "convertNovel" in response.text
    assert "analyzeCharacters" in response.text
    # 六种操作的中文文案从 HTML 按钮迁到这里：解析结果以"我理解为：加强戏剧冲突"呈现
    assert "重新生成本场对白" in response.text
    assert "加强戏剧冲突" in response.text
    assert "减少旁白" in response.text
