import json

import httpx
import pytest

from story2script.agent import (
    AGENT_PROMPT_MARKER,
    ROLE_ADAPTER,
    ROLE_CONTINUITY,
    ROLE_REVIEWER,
    SUPERVISOR,
    TEAM_PROMPT_MARKER,
    AdaptationTeam,
    AgentSessionStore,
    Blackboard,
    SpecialistTask,
    build_specialists,
)
from story2script.continuity import CONTINUITY_PROMPT_MARKER
from story2script.converter import DemoConverter
from story2script.metrics import metrics
from story2script.parser import parse_chapters
from story2script.scene_review import REVIEW_PROMPT_MARKER

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"


def sample_screenplay():
    return DemoConverter().convert(parse_chapters(NOVEL), title="测试故事", genre="剧情")


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def ai_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


def review_payload(score: float, verdict: str) -> dict:
    return {
        "scores": {
            "dramatization": score,
            "dialogue_conflict": score,
            "residual_narration": score,
            "character_voice": score,
        },
        "verdict": verdict,
        "issues": [] if verdict == "pass" else ["冲突不足"],
        "suggested_operation": "strengthen_conflict",
        "feedback": "加强对抗",
    }


def dispatched_roles(result) -> list[str]:
    return [
        step.action.params["role"]
        for step in result.trace
        if step.action is not None and step.action.tool == "dispatch"
    ]


# ---------------------------------------------------------------- demo 派单


def test_demo_team_runs_full_collaboration():
    notes: list[tuple[int, int, str]] = []
    team = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=6)

    outcome = team.run(
        sample_screenplay(), goal="全场景达标", progress_cb=lambda r, m, n: notes.append((r, m, n))
    )
    result = outcome.result

    assert result.status in {"completed", "budget_exhausted"}
    # 三个专职都参与过，且主管的派单在轨迹里可追溯
    assert set(result.role_summaries) == {ROLE_REVIEWER, ROLE_CONTINUITY, ROLE_ADAPTER}
    assert {step.role for step in result.trace} == {
        SUPERVISOR,
        ROLE_REVIEWER,
        ROLE_CONTINUITY,
        ROLE_ADAPTER,
    }
    assert notes and notes[0][0] == 1


def test_demo_dispatch_order_starts_with_review_then_continuity():
    team = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=6)

    roles = dispatched_roles(team.run(sample_screenplay(), goal="全场景达标").result)

    assert roles[:3] == [ROLE_REVIEWER, ROLE_CONTINUITY, ROLE_ADAPTER]


def test_demo_dispatch_is_deterministic():
    first = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=6).run(sample_screenplay())
    second = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=6).run(sample_screenplay())

    assert dispatched_roles(first.result) == dispatched_roles(second.result)
    assert first.result.status == second.result.status


def test_demo_team_finishes_early_when_quality_met():
    team = AdaptationTeam(mode="demo", threshold=0.1, max_rounds=6)

    result = team.run(sample_screenplay()).result

    assert result.status == "completed"
    # 审校 + 一致性两轮即可确认达标，不必派改编
    assert ROLE_ADAPTER not in dispatched_roles(result)
    assert result.rounds_used <= 3


def test_demo_team_exhausts_rounds():
    team = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=2)

    result = team.run(sample_screenplay()).result

    assert result.status == "budget_exhausted"
    assert result.rounds_used == 2
    assert "轮次上限" in result.message


def test_messages_form_dispatch_report_pairs():
    result = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=6).run(
        sample_screenplay()
    ).result

    assert [item.seq for item in result.messages] == list(
        range(1, len(result.messages) + 1)
    )
    dispatches = [item for item in result.messages if item.kind == "dispatch"]
    reports = [item for item in result.messages if item.kind == "report"]
    assert len(dispatches) == len(reports)
    assert all(item.sender == SUPERVISOR for item in dispatches)
    assert all(item.recipient == SUPERVISOR for item in reports)


def test_team_run_records_metrics():
    AdaptationTeam(mode="demo", threshold=9.5, max_rounds=4).run(sample_screenplay())

    row = metrics.summary()["tasks"]["team_run"]
    assert row["calls"] == 1
    event = next(item for item in metrics.recent_events() if item.get("kind") == "team_run")
    assert event["extra"]["rounds"] >= 1
    assert sorted(event["extra"]["roles"])


def test_unsupported_mode_rejected():
    with pytest.raises(ValueError, match="不支持的团队模式"):
        AdaptationTeam(mode="magic")


def test_team_session_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    store = AgentSessionStore()

    outcome = AdaptationTeam(mode="demo", threshold=9.5, max_rounds=4).run(
        sample_screenplay(), goal="留档", session_store=store
    )
    session_id = outcome.result.session_id

    assert session_id.startswith("mag-")
    loaded = store.load_team(session_id)
    assert loaded["goal"] == "留档"
    assert loaded["result"].rounds_used == outcome.result.rounds_used
    assert len(loaded["screenplay"].scenes) == len(outcome.screenplay.scenes)

    # 协作会话与单体会话互不串台
    assert [item["session_id"] for item in store.list_team_sessions()] == [session_id]
    assert store.list_sessions() == []


# ---------------------------------------------------------------- 黑板


def test_blackboard_digest_is_bounded():
    blackboard = Blackboard(screenplay=sample_screenplay(), goal="目标", threshold=7.0)
    for index in range(20):
        blackboard.post(SUPERVISOR, ROLE_REVIEWER, f"第 {index} 条消息")

    digest = blackboard.digest()

    assert len(digest["recent_messages"]) <= 8
    assert digest["reviewed"] is False
    assert digest["continuity_checked"] is False


def test_specialists_write_results_to_blackboard():
    blackboard = Blackboard(screenplay=sample_screenplay(), threshold=9.5)
    specialists = build_specialists(mode="demo", threshold=9.5)

    review = specialists[ROLE_REVIEWER].run(blackboard, SpecialistTask(role=ROLE_REVIEWER))
    assert blackboard.report is not None
    assert "机审完成" in review.summary
    assert blackboard.digest()["reviewed"] is True

    continuity = specialists[ROLE_CONTINUITY].run(
        blackboard, SpecialistTask(role=ROLE_CONTINUITY)
    )
    assert "一致性检查完成" in continuity.summary
    assert blackboard.digest()["continuity_checked"] is True

    before = blackboard.screenplay
    adapter = specialists[ROLE_ADAPTER].run(blackboard, SpecialistTask(role=ROLE_ADAPTER))
    assert adapter.changed_screenplay is True
    assert all(step.role == ROLE_ADAPTER for step in adapter.steps)
    assert blackboard.screenplay is not before


# ---------------------------------------------------------------- ai 派单


def test_ai_supervisor_dispatch_sequence(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    supervisor_prompts: list[str] = []
    decisions = [
        {"thought": "先摸底", "dispatch": {"role": ROLE_REVIEWER, "instruction": "全量机审"}},
        {"thought": "查矛盾", "dispatch": {"role": ROLE_CONTINUITY, "instruction": "查一致性"}},
        {"thought": "收工", "dispatch": {"role": "finish", "instruction": "已确认质量与一致性"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if TEAM_PROMPT_MARKER in prompt:
            supervisor_prompts.append(prompt)
            return ai_response(decisions[len(supervisor_prompts) - 1])
        if REVIEW_PROMPT_MARKER in prompt:
            # 返回不达标：否则质量与一致性双双达标会让协作提前短路，
            # 主管的第三个决策（显式 finish）就走不到了。
            return ai_response(review_payload(3.0, "fail"))
        if CONTINUITY_PROMPT_MARKER in prompt:
            return ai_response({"findings": []})
        raise AssertionError(f"未预期的请求：{prompt[:60]}")

    team = AdaptationTeam(
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=5,
    )
    result = team.run(sample_screenplay(), goal="协作目标").result

    assert result.status == "completed"
    assert dispatched_roles(result) == [ROLE_REVIEWER, ROLE_CONTINUITY]
    assert result.message == "已确认质量与一致性"  # 来自主管的 finish 指令
    assert result.llm_calls == 3  # 三次主管决策
    # 主管提示词里带黑板状态与角色名册
    assert "黑板状态" in supervisor_prompts[0]
    assert ROLE_ADAPTER in supervisor_prompts[0]


def test_ai_supervisor_self_corrects_invalid_role(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    supervisor_prompts: list[str] = []
    decisions = [
        {"thought": "试个不存在的角色", "dispatch": {"role": "translator"}},
        {"thought": "改派审校", "dispatch": {"role": ROLE_REVIEWER}},
        {"thought": "收工", "dispatch": {"role": "finish", "instruction": "完成"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if TEAM_PROMPT_MARKER in prompt:
            supervisor_prompts.append(prompt)
            return ai_response(decisions[len(supervisor_prompts) - 1])
        if REVIEW_PROMPT_MARKER in prompt:
            return ai_response(review_payload(9.0, "pass"))
        if CONTINUITY_PROMPT_MARKER in prompt:
            return ai_response({"findings": []})
        raise AssertionError(f"未预期的请求：{prompt[:60]}")

    result = AdaptationTeam(
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=5,
    ).run(sample_screenplay()).result

    # 非法角色不抛异常，而是作为错误留在轨迹里，主管下一轮自我修正
    assert any("不支持的角色" in step.error for step in result.trace)
    assert ROLE_REVIEWER in dispatched_roles(result)
    assert result.status == "completed"


def test_ai_supervisor_circuit_breaks_after_repeated_errors(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if TEAM_PROMPT_MARKER in prompt:
            return ai_response({"thought": "坏决策", "dispatch": {"role": "nobody"}})
        return ai_response(review_payload(9.0, "pass"))

    result = AdaptationTeam(
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=8,
    ).run(sample_screenplay()).result

    assert result.status == "failed"
    assert "无效派单" in result.message
    assert result.rounds_used == 3


def test_ai_supervisor_tolerates_non_json(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if TEAM_PROMPT_MARKER in prompt:
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(200, json={"choices": [{"message": {"content": "抱歉"}}]})
            return ai_response({"thought": "收工", "dispatch": {"role": "finish"}})
        return ai_response(review_payload(9.0, "pass"))

    result = AdaptationTeam(
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=5,
    ).run(sample_screenplay()).result

    assert any(step.error for step in result.trace)
    assert result.status == "completed"


def test_ai_adapter_llm_calls_are_aggregated(monkeypatch: pytest.MonkeyPatch):
    """改编专职内部的 planner 调用要计入协作总数。"""
    configure_ai(monkeypatch)
    supervisor_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if TEAM_PROMPT_MARKER in prompt:
            supervisor_calls["count"] += 1
            if supervisor_calls["count"] == 1:
                return ai_response({"thought": "派改编", "dispatch": {"role": ROLE_ADAPTER}})
            return ai_response({"thought": "收工", "dispatch": {"role": "finish"}})
        if AGENT_PROMPT_MARKER in prompt:
            return ai_response({"thought": "结束", "action": {"tool": "finish", "params": {}}})
        if REVIEW_PROMPT_MARKER in prompt:
            # 不达标才会让改编专职真正进入 planner 循环（达标它会直接返回）。
            return ai_response(review_payload(3.0, "fail"))
        if CONTINUITY_PROMPT_MARKER in prompt:
            return ai_response({"findings": []})
        raise AssertionError(f"未预期的请求：{prompt[:60]}")

    result = AdaptationTeam(
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        threshold=7.0,
        max_rounds=4,
        max_steps_per_agent=2,
    ).run(sample_screenplay()).result

    # 2 次主管决策 + 改编内部至少 1 次 planner 调用
    assert result.llm_calls >= 3
    assert ROLE_ADAPTER in result.role_summaries
