import json

import httpx
import pytest

from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.scene_review import (
    DemoSceneReviewer,
    HumanVerdict,
    ReviewReport,
    SceneReviewResult,
    get_scene_reviewer,
    merge_human_verdicts,
    review_and_improve,
    review_scenes_report,
    review_screenplay,
)


def sample_screenplay():
    chapters = parse_chapters(
        "第一章 开始\n林夏说：“出发吧。”\n"
        "第二章 转折\n雨落下来。\n"
        "第三章 结局\n太阳升起。"
    )
    return DemoConverter().convert(chapters, title="测试故事", genre="剧情")


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def ai_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


def test_demo_reviewer_is_deterministic() -> None:
    screenplay = sample_screenplay()
    reviewer = DemoSceneReviewer()

    first = reviewer.review_scene(screenplay, screenplay.scenes[0], threshold=7.0)
    second = reviewer.review_scene(screenplay, screenplay.scenes[0], threshold=7.0)

    assert first == second
    assert first.scene_id == "scene-1"
    assert set(first.scores) == {
        "dramatization",
        "dialogue_conflict",
        "residual_narration",
        "character_voice",
    }
    assert all(0.0 <= value <= 10.0 for value in first.scores.values())
    assert first.verdict in ("pass", "fail")
    assert first.suggested_operation


def test_review_screenplay_covers_all_scenes_and_builds_report() -> None:
    screenplay = sample_screenplay()
    report = review_scenes_report(screenplay, mode="demo", threshold=7.0)

    assert set(report.machine) == {scene.id for scene in screenplay.scenes}
    assert report.summary["scene_count"] == len(screenplay.scenes)
    assert report.summary["pass_count"] + report.summary["fail_count"] == len(screenplay.scenes)
    assert report.threshold == 7.0
    assert report.rounds_used == 1


def test_review_screenplay_rejects_unknown_scene_ids() -> None:
    screenplay = sample_screenplay()
    reviewer = DemoSceneReviewer()

    with pytest.raises(ValueError, match="scene-99"):
        review_screenplay(screenplay, reviewer, threshold=7.0, scene_ids=["scene-99"])


def test_get_scene_reviewer_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="不支持的审校模式"):
        get_scene_reviewer("magic")


def test_ai_reviewer_parses_scores_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["prompt"] = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        return ai_response(
            {
                "scores": {
                    "dramatization": 8,
                    "dialogue_conflict": 6,
                    "residual_narration": 9,
                    "character_voice": 7,
                },
                "issues": ["对白对抗性不足"],
                "verdict": "pass",
                "suggested_operation": "strengthen_conflict",
                "feedback": "让对白直接顶撞目标。",
            }
        )

    reviewer = get_scene_reviewer("ai", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = reviewer.review_scene(screenplay, screenplay.scenes[0], threshold=7.0)

    assert "请对以下场景进行审校评分" in captured["prompt"]
    assert "scene-1" in captured["prompt"]
    assert result.total == 7.5
    assert result.verdict == "pass"
    assert result.suggested_operation == "strengthen_conflict"
    assert result.feedback == "让对白直接顶撞目标。"
    assert result.issues == ["对白对抗性不足"]


def test_ai_reviewer_clamps_scores_and_falls_back_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()

    def handler(request: httpx.Request) -> httpx.Response:
        return ai_response(
            {
                "scores": {
                    "dramatization": 99,
                    "dialogue_conflict": -3,
                    "residual_narration": "not-a-number",
                    "character_voice": 8,
                },
                "verdict": "maybe",
                "suggested_operation": "delete_scene",
            }
        )

    reviewer = get_scene_reviewer("ai", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = reviewer.review_scene(screenplay, screenplay.scenes[0], threshold=7.0)

    assert result.scores["dramatization"] == 10.0
    assert result.scores["dialogue_conflict"] == 0.0
    assert result.scores["residual_narration"] == 5.0
    # 非法 verdict 按平均分与阈值推导；非法 operation 回退到最低分项映射。
    assert result.verdict == ("pass" if result.total >= 7.0 else "fail")
    assert result.suggested_operation == "strengthen_conflict"


def test_ai_reviewer_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "这不是 JSON"}}]}
        )

    reviewer = get_scene_reviewer("ai", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="AI 审校失败"):
        reviewer.review_scene(screenplay, screenplay.scenes[0], threshold=7.0)


def test_review_and_improve_rewrites_failing_scene_until_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()
    review_calls: list[str] = []

    replacement = screenplay.scenes[0].model_copy(deep=True)
    replacement.conflict = "AI冲突：阻力升级。"

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "请对以下场景进行审校评分" in prompt:
            scene_id = "scene-1" if '"id": "scene-1"' in prompt else "other"
            review_calls.append(scene_id)
            failing_first_round = scene_id == "scene-1" and review_calls.count("scene-1") == 1
            score = 3 if failing_first_round else 9
            return ai_response(
                {
                    "scores": {criterion: score for criterion in (
                        "dramatization",
                        "dialogue_conflict",
                        "residual_narration",
                        "character_voice",
                    )},
                    "issues": ["对白没有推动冲突"] if failing_first_round else [],
                    "verdict": "fail" if failing_first_round else "pass",
                    "suggested_operation": "strengthen_conflict",
                    "feedback": "加强对抗。",
                }
            )
        # 重写请求：必须携带审校意见。
        assert "审校意见（请重点修正以下问题）：加强对抗。" in prompt
        return ai_response(replacement.model_dump(mode="json"))

    updated, report = review_and_improve(
        screenplay,
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=2,
    )

    assert report.rounds_used == 2
    assert report.machine["scene-1"].verdict == "pass"
    assert report.machine["scene-1"].round == 2
    assert updated.scenes[0].conflict == "AI冲突：阻力升级。"
    assert report.summary["fail_count"] == 0
    # 其余场景只审一次。
    assert review_calls.count("scene-1") == 2


def test_review_and_improve_survives_rewrite_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "请对以下场景进行审校评分" in prompt:
            return ai_response(
                {
                    "scores": {criterion: 3 for criterion in (
                        "dramatization",
                        "dialogue_conflict",
                        "residual_narration",
                        "character_voice",
                    )},
                    "verdict": "fail",
                    "suggested_operation": "reduce_narration",
                    "feedback": "问题很多。",
                }
            )
        # 重写请求全部返回坏 JSON，触发单场景失败兜底。
        return httpx.Response(200, json={"choices": [{"message": {"content": "坏掉了"}}]})

    updated, report = review_and_improve(
        screenplay,
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=3,
    )

    assert report.rounds_used == 1
    assert all(result.verdict == "fail" for result in report.machine.values())
    assert any("自动修正失败" in issue for issue in report.machine["scene-1"].issues)
    # 剧本保持原样且仍合法。
    assert updated.scenes[0].model_dump() == screenplay.scenes[0].model_dump()


def test_review_and_improve_demo_mode_terminates() -> None:
    screenplay = sample_screenplay()

    updated, report = review_and_improve(screenplay, mode="demo", threshold=9.5, max_rounds=2)

    assert report.rounds_used <= 2
    assert set(report.machine) == {scene.id for scene in updated.scenes}


def test_review_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_THRESHOLD", "9.5")
    monkeypatch.setenv("AI_REVIEW_MAX_ROUNDS", "1")
    screenplay = sample_screenplay()

    _updated, report = review_and_improve(screenplay, mode="demo")

    assert report.threshold == 9.5
    assert report.rounds_used == 1


def test_merge_human_verdicts_updates_report() -> None:
    report = ReviewReport(
        threshold=7.0,
        rounds_used=1,
        machine={
            "scene-1": SceneReviewResult(
                scene_id="scene-1",
                scores={criterion: 8.0 for criterion in (
                    "dramatization",
                    "dialogue_conflict",
                    "residual_narration",
                    "character_voice",
                )},
                total=8.0,
                verdict="pass",
            )
        },
        summary={"pass_count": 1, "fail_count": 0},
    )

    merged = merge_human_verdicts(
        report,
        [HumanVerdict(scene_id="scene-1", status="rejected", comment="节奏太慢")],
    )

    assert merged.human["scene-1"].status == "rejected"
    assert merged.human["scene-1"].comment == "节奏太慢"
    assert merged.summary["human_rejected"] == 1
    assert merged.summary["human_approved"] == 0
    # 原报告不被就地修改。
    assert "scene-1" not in report.human


def test_merge_human_verdicts_rejects_unknown_scene() -> None:
    report = ReviewReport(
        threshold=7.0,
        rounds_used=1,
        machine={
            "scene-1": SceneReviewResult(
                scene_id="scene-1",
                scores={},
                total=0.0,
                verdict="fail",
            )
        },
    )

    with pytest.raises(ValueError, match="scene-9"):
        merge_human_verdicts(report, [HumanVerdict(scene_id="scene-9", status="approved")])
