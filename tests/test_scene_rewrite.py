import json

import httpx
import pytest

from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.scene_rewrite import rewrite_scene
from story2script.screenplay import Action, Dialogue


def sample_screenplay():
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )
    return DemoConverter().convert(chapters, title="测试故事", genre="剧情")


def test_rewrite_scene_updates_only_target_scene_dialogue() -> None:
    screenplay = sample_screenplay()
    untouched_scene = screenplay.scenes[1].model_dump(mode="json")

    updated, message = rewrite_scene(screenplay, "scene-1", "rewrite_dialogue")
    dialogue = updated.scenes[0].elements[1]

    assert message == "已重新生成本场对白。"
    assert isinstance(dialogue, Dialogue)
    assert "把话说清楚" in dialogue.text
    assert updated.scenes[1].model_dump(mode="json") == untouched_scene


def test_rewrite_scene_can_strengthen_conflict() -> None:
    screenplay = sample_screenplay()

    updated, _ = rewrite_scene(screenplay, "scene-1", "strengthen_conflict")

    assert "冲突升级" in updated.scenes[0].conflict
    assert "冲突升级" in updated.scenes[0].beat
    assert isinstance(updated.scenes[0].elements[0], Action)


def test_rewrite_scene_can_adjust_character_voice() -> None:
    screenplay = sample_screenplay()
    character_id = screenplay.characters[0].id

    updated, _ = rewrite_scene(
        screenplay,
        "scene-1",
        "adjust_character_voice",
        character_id=character_id,
        tone="更锋利",
    )
    dialogue = updated.scenes[0].elements[1]

    assert isinstance(dialogue, Dialogue)
    assert dialogue.character == character_id
    assert dialogue.parenthetical == "更锋利"
    assert dialogue.emotion == "更锋利"


def test_rewrite_scene_rejects_unknown_scene() -> None:
    screenplay = sample_screenplay()

    try:
        rewrite_scene(screenplay, "missing-scene", "add_camera_hints")
    except ValueError as exc:
        assert "missing-scene" in str(exc)
    else:
        raise AssertionError("Expected missing scene to raise ValueError")


def test_rewrite_scene_can_use_ai_for_single_scene(monkeypatch: pytest.MonkeyPatch) -> None:
    screenplay = sample_screenplay()
    replacement = screenplay.scenes[0].model_copy(deep=True)
    replacement.conflict = "AI冲突：新的阻力让角色无法直接达成目标。"
    replacement.camera_hints.append("AI镜头：近景捕捉角色迟疑。")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                replacement.model_dump(mode="json"),
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    updated, message = rewrite_scene(
        screenplay,
        "scene-1",
        "strengthen_conflict",
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    prompt = captured["payload"]["messages"][0]["content"]
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert "只返回一个符合 Story2Script Scene Schema 的 JSON 对象" in prompt
    assert "加强本场戏剧冲突" in prompt
    assert "global_state" in prompt
    assert "int_ext、time_of_day、location 必须保持不变" in prompt
    assert updated.scenes[0].conflict.startswith("AI冲突")
    assert updated.scenes[1].model_dump(mode="json") == screenplay.scenes[1].model_dump(mode="json")
    assert message == "AI 已加强本场戏剧冲突。"


def test_ai_scene_rewrite_rejects_changed_scene_id(monkeypatch: pytest.MonkeyPatch) -> None:
    screenplay = sample_screenplay()
    replacement = screenplay.scenes[0].model_copy(deep=True)
    replacement.id = "scene-99"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                replacement.model_dump(mode="json"),
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    with pytest.raises(ValueError, match="scene id"):
        rewrite_scene(
            screenplay,
            "scene-1",
            "add_camera_hints",
            mode="ai",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )


def test_ai_scene_rewrite_rejects_changed_location(monkeypatch: pytest.MonkeyPatch) -> None:
    screenplay = sample_screenplay()
    replacement = screenplay.scenes[0].model_copy(deep=True)
    replacement.location = "另一个地点"
    replacement.heading = f"{replacement.int_ext} {replacement.location} - {replacement.time_of_day}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                replacement.model_dump(mode="json"),
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    with pytest.raises(ValueError, match="location"):
        rewrite_scene(
            screenplay,
            "scene-1",
            "add_camera_hints",
            mode="ai",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
