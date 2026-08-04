"""API 配置：多套配置的保存 / 切换 / 删除、密钥遮罩与静默失效的暴露。"""

import json

import pytest
from fastapi.testclient import TestClient

from story2script import main as main_module
from story2script import provider_config
from story2script.llm_client import LLMClient
from story2script.main import app

client = TestClient(app)

SECRET = "sk-secret-abcd1234"


def deepseek_fields(**overrides) -> dict:
    fields = {
        "AI_BASE_URL": "https://api.deepseek.com/v1",
        "AI_MODEL": "deepseek-chat",
        "AI_API_KEY": SECRET,
    }
    fields.update(overrides)
    return fields


def read_env() -> str:
    path = provider_config.env_path()
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def read_store() -> dict:
    return json.loads(provider_config.providers_path().read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 遮罩与校验


def test_secret_is_masked_not_truncated_to_nothing() -> None:
    assert provider_config.mask_secret(SECRET) == "••••1234"
    assert provider_config.mask_secret("") == ""
    # 短密钥整体遮掉，不泄漏尾部
    assert provider_config.mask_secret("sk-123") == "••••••"


@pytest.mark.parametrize("name", ["", "   ", "a/b", "../etc/passwd", "x" * 41])
def test_bad_profile_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        provider_config.validate_profile_name(name)


def test_non_whitelisted_keys_are_dropped() -> None:
    # 否则这就成了「经 HTTP 往磁盘写任意环境变量」的口子
    cleaned = provider_config.sanitize_fields(
        {"AI_MODEL": "m", "PATH": "/tmp", "STORY2SCRIPT_API_TOKEN": "x"}
    )
    assert cleaned == {"AI_MODEL": "m"}


# ---------------------------------------------------------------- 保存与切换


def test_save_then_activate_writes_env() -> None:
    provider_config.save_profile("deepseek", deepseek_fields())
    # 只保存不启用时不应改动 .env
    assert "deepseek-chat" not in read_env()

    provider_config.activate_profile("deepseek")
    env = read_env()
    assert "AI_MODEL=deepseek-chat" in env
    # .env 里必须是明文，LLMClient 要用它发请求
    assert f"AI_API_KEY={SECRET}" in env


def test_switching_profiles_removes_stale_keys() -> None:
    """白名单键按全量替换写入：否则上一套的 AI_REASONING_EFFORT 会残留、跨配置串味。"""
    provider_config.save_profile(
        "reasoner", deepseek_fields(AI_REASONING_EFFORT="high"), activate=True
    )
    assert "AI_REASONING_EFFORT=high" in read_env()

    provider_config.save_profile(
        "kimi",
        {
            "AI_BASE_URL": "https://api.moonshot.cn/v1",
            "AI_MODEL": "kimi-k2",
            "AI_API_KEY": "sk-kimi-9999",
        },
        activate=True,
    )
    env = read_env()
    assert "AI_MODEL=kimi-k2" in env
    assert "AI_REASONING_EFFORT" not in env
    assert SECRET not in env


def test_env_write_preserves_unrelated_lines() -> None:
    path = provider_config.env_path()
    path.write_text(
        "# 我的注释\nSTORY2SCRIPT_API_TOKEN=keep-me\nAI_MODEL=old-model\n", encoding="utf-8"
    )

    provider_config.save_profile("deepseek", deepseek_fields(), activate=True)
    env = read_env()

    # 本模块不管的键与注释必须留下
    assert "# 我的注释" in env
    assert "STORY2SCRIPT_API_TOKEN=keep-me" in env
    # 白名单内的旧值被替换而不是重复出现
    assert "AI_MODEL=old-model" not in env
    assert env.count("AI_MODEL=") == 1


def test_blank_secret_keeps_existing_key() -> None:
    """前端拿到的是遮罩值，原样回传不应把密钥冲掉。"""
    provider_config.save_profile("deepseek", deepseek_fields())
    provider_config.save_profile("deepseek", {"AI_MODEL": "deepseek-reasoner", "AI_API_KEY": ""})

    stored = read_store()["profiles"]["deepseek"]
    assert stored["AI_API_KEY"] == SECRET
    assert stored["AI_MODEL"] == "deepseek-reasoner"


def test_masked_secret_roundtrip_does_not_overwrite_key() -> None:
    """把读到的遮罩值原样交回来，不能把真密钥覆盖成 "••••1234"。

    前端刻意不回填密钥框，但服务端必须自己兜住：任何「GET 读出来再 POST 回去」的
    客户端（脚本、另一个前端、浏览器密码管理器自动填充）都会踩到，而一旦按字面
    存下，原密钥就永久丢失了。
    """
    provider_config.save_profile("deepseek", deepseek_fields())
    masked = provider_config.list_profiles()["profiles"][0]["fields"]["AI_API_KEY"]
    assert masked == "••••1234"

    # 模拟客户端把整份读到的 fields 原样回传
    provider_config.save_profile("deepseek", {"AI_MODEL": "deepseek-chat", "AI_API_KEY": masked})

    assert read_store()["profiles"]["deepseek"]["AI_API_KEY"] == SECRET


def test_masked_secret_detection_never_hits_real_keys() -> None:
    # 真实密钥不含 •，所以这个判断没有误伤风险
    assert provider_config.looks_like_masked_secret("••••1234") is True
    assert provider_config.looks_like_masked_secret("••••") is True
    assert provider_config.looks_like_masked_secret(SECRET) is False
    assert provider_config.looks_like_masked_secret("") is False


def test_masked_roundtrip_via_api_keeps_key_working() -> None:
    """走 REST 的同一场景：改模型时把遮罩密钥一起提交，配置不该变成缺密钥。"""
    client.post("/api/providers", json={"name": "deepseek", "fields": deepseek_fields()})
    masked = client.get("/api/providers").json()["profiles"][0]["fields"]["AI_API_KEY"]

    response = client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "fields": {"AI_MODEL": "deepseek-reasoner", "AI_API_KEY": masked},
            "activate": True,
        },
    )

    assert response.status_code == 200
    profile = response.json()["profiles"][0]
    assert profile["missing_fields"] == []
    assert profile["has_api_key"] is True
    # 落到 .env 的仍是可用的明文密钥，而不是遮罩串
    env = read_env()
    assert f"AI_API_KEY={SECRET}" in env
    assert "•" not in env


def test_submitted_blank_clears_non_secret_field() -> None:
    """「提交了但为空」与「没提交」是两回事：前者表示清空这一项。"""
    provider_config.save_profile(
        "deepseek", deepseek_fields(AI_REASONING_EFFORT="high", AI_MAX_TOKENS="4096")
    )
    # 只清 AI_REASONING_EFFORT，不提交 AI_MAX_TOKENS
    provider_config.save_profile("deepseek", {"AI_REASONING_EFFORT": ""})

    stored = read_store()["profiles"]["deepseek"]
    assert "AI_REASONING_EFFORT" not in stored
    assert stored["AI_MAX_TOKENS"] == "4096"   # 没提交的字段保持不变


def test_activate_requires_the_three_essentials() -> None:
    provider_config.save_profile("half", {"AI_BASE_URL": "https://x.test/v1"})
    with pytest.raises(ValueError, match="缺少必填项"):
        provider_config.activate_profile("half")


def test_delete_clears_active_without_breaking_env() -> None:
    """删掉正在生效的配置只清激活标记，不动 .env——否则工作台会立刻不可用。"""
    provider_config.save_profile("deepseek", deepseek_fields(), activate=True)
    provider_config.delete_profile("deepseek")

    data = provider_config.list_profiles()
    assert data["active"] == ""
    assert data["profiles"] == []
    # .env 仍然可用
    assert "AI_MODEL=deepseek-chat" in read_env()


def test_delete_unknown_profile_raises() -> None:
    with pytest.raises(KeyError):
        provider_config.delete_profile("nope")


def test_corrupt_store_degrades_to_empty() -> None:
    """配置清单坏了不该让工作台起不来。"""
    path = provider_config.providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert provider_config.list_profiles()["profiles"] == []


# ---------------------------------------------------------------- 静默失效


def test_process_env_shadowing_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """os.getenv 优先于 .env：被遮盖的字段改了不生效，必须让用户看到。"""
    monkeypatch.setenv("AI_MODEL", "from-shell")
    assert "AI_MODEL" in provider_config.shadowed_fields()

    monkeypatch.delenv("AI_MODEL")
    assert "AI_MODEL" not in provider_config.shadowed_fields()


def test_disabled_dotenv_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """DISABLE_DOTENV 下 .env 完全不被读取，切换会「写了但没效果」。"""
    monkeypatch.setenv("STORY2SCRIPT_DISABLE_DOTENV", "1")
    assert provider_config.dotenv_disabled() is True
    assert provider_config.list_profiles()["dotenv_disabled"] is True


# ---------------------------------------------------------------- 生效链路


def test_written_env_is_what_llm_client_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """切换之所以「实时」：LLMClient 每次新建都重读 .env，无需重启。"""
    # conftest 的 disable_default_dotenv 给所有测试关掉了 .env 读取（免得误读开发者
    # 本机的真实配置）。这条测试要验的恰好是生效链路本身，所以放开它——此时
    # isolate_provider_config 已把 .env 指向 tmp 目录，读的不是真实文件。
    monkeypatch.delenv("STORY2SCRIPT_DISABLE_DOTENV", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)

    provider_config.save_profile("deepseek", deepseek_fields(), activate=True)
    assert LLMClient(load_dotenv=True).model == "deepseek-chat"

    provider_config.save_profile(
        "kimi",
        {
            "AI_BASE_URL": "https://api.moonshot.cn/v1",
            "AI_MODEL": "kimi-k2",
            "AI_API_KEY": "sk-kimi-9999",
        },
        activate=True,
    )
    # 同一进程内、没有重启：新建的 client 立刻看到新配置
    assert LLMClient(load_dotenv=True).model == "kimi-k2"


def test_overrides_beat_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """连通性测试依赖这一点：否则 shell 里残留的 AI_MODEL 会让「测配置 B」测成别的。"""
    monkeypatch.setenv("AI_MODEL", "from-shell")
    llm = LLMClient(load_dotenv=False, overrides={"AI_MODEL": "under-test"})
    assert llm.model == "under-test"

    # 不传 overrides 时行为不变（既有调用方零影响）
    assert LLMClient(load_dotenv=False).model == "from-shell"


# ---------------------------------------------------------------- REST 路由


def test_api_never_returns_plaintext_secret() -> None:
    response = client.post(
        "/api/providers", json={"name": "deepseek", "fields": deepseek_fields()}
    )

    assert response.status_code == 200
    assert SECRET not in response.text
    profile = response.json()["profiles"][0]
    assert profile["fields"]["AI_API_KEY"] == "••••1234"
    assert profile["has_api_key"] is True


def test_api_activate_and_list() -> None:
    client.post("/api/providers", json={"name": "deepseek", "fields": deepseek_fields()})
    activated = client.post("/api/providers/activate", json={"name": "deepseek"})

    assert activated.status_code == 200
    assert activated.json()["active"] == "deepseek"

    listed = client.get("/api/providers").json()
    assert listed["active"] == "deepseek"
    assert listed["current"]["AI_MODEL"] == "deepseek-chat"
    # current 里的密钥同样只出遮罩值
    assert listed["current"]["AI_API_KEY"] == "••••1234"


def test_api_rejects_unknown_and_invalid() -> None:
    assert client.post("/api/providers/activate", json={"name": "nope"}).status_code == 404
    assert client.post("/api/providers/delete", json={"name": "nope"}).status_code == 404
    assert (
        client.post("/api/providers", json={"name": "a/b", "fields": {}}).status_code == 422
    )


def test_api_test_endpoint_reports_config_error_without_network() -> None:
    """缺密钥时应返回 ok=false 的可读原因，而不是 500。"""
    client.post(
        "/api/providers",
        json={"name": "half", "fields": {"AI_BASE_URL": "https://x.test/v1", "AI_MODEL": "m"}},
    )
    response = client.post("/api/providers/test", json={"name": "half"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "AI_API_KEY" in body["message"]


def test_api_test_endpoint_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeLLM:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.client = type("C", (), {"close": lambda self: None})()

        def complete_json(self, prompt: str, use_cache: bool = True) -> str:
            captured["use_cache"] = use_cache
            return '{"ok": true}'

    monkeypatch.setattr(main_module, "LLMClient", FakeLLM)
    client.post("/api/providers", json={"name": "deepseek", "fields": deepseek_fields()})

    body = client.post("/api/providers/test", json={"name": "deepseek"}).json()

    assert body["ok"] is True
    assert body["model"] == "deepseek-chat"
    # 待测配置经 overrides 注入，且绕过缓存（否则第二次测试拿到上次结果）
    assert captured["overrides"]["AI_MODEL"] == "deepseek-chat"
    assert captured["load_dotenv"] is False
    assert captured["use_cache"] is False


def test_api_test_endpoint_unknown_profile() -> None:
    assert client.post("/api/providers/test", json={"name": "nope"}).status_code == 404
