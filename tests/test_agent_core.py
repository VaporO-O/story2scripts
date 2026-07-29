import json

import httpx
import pytest

from story2script.agent import (
    AGENT_PROMPT_MARKER,
    AdaptationAgent,
    AgentAction,
    AgentSessionStore,
    AgentStep,
    Scratchpad,
)
from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.scene_review import REVIEW_PROMPT_MARKER

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"


def sample_screenplay():
    chapters = parse_chapters(NOVEL)
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


def text_response(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def review_payload(score: float, verdict: str) -> dict:
    return {
        "scores": {
            "dramatization": score,
            "dialogue_conflict": score,
            "residual_narration": score,
            "character_voice": score,
        },
        "verdict": verdict,
        "issues": ["节奏拖沓"] if verdict == "fail" else [],
        "suggested_operation": "strengthen_conflict",
        "feedback": "加强对抗",
    }


# ---------------------------------------------------------------- demo 模式


def test_demo_agent_improves_failing_screenplay():
    screenplay = sample_screenplay()

    agent = AdaptationAgent(mode="demo", threshold=9.5, max_steps=12)
    outcome = agent.run(screenplay, goal="全场景达标")

    result = outcome.result
    assert result.status in {"completed", "budget_exhausted"}
    assert result.goal == "全场景达标"
    assert result.llm_calls == 0
    assert result.steps_used == len(result.trace) > 0
    assert result.initial_summary["scene_count"] == len(screenplay.scenes)
    assert result.final_summary["avg_score"] >= result.initial_summary["avg_score"]
    assert outcome.report is not None
    tools_used = {step.action.tool for step in result.trace if step.action}
    assert "rewrite_scene" in tools_used
    assert "review_screenplay" in tools_used


def test_demo_agent_is_deterministic():
    first = AdaptationAgent(mode="demo", threshold=9.5, max_steps=12).run(sample_screenplay())
    second = AdaptationAgent(mode="demo", threshold=9.5, max_steps=12).run(sample_screenplay())

    def action_sequence(outcome):
        return [
            (step.action.tool, json.dumps(step.action.params, sort_keys=True))
            for step in outcome.result.trace
            if step.action
        ]

    assert action_sequence(first) == action_sequence(second)
    assert first.result.status == second.result.status


def test_demo_agent_finishes_immediately_when_goal_met():
    outcome = AdaptationAgent(mode="demo", threshold=0.1, max_steps=12).run(sample_screenplay())

    assert outcome.result.status == "completed"
    assert outcome.result.steps_used == 0
    assert "初始审校已达标" in outcome.result.message


def test_demo_agent_budget_exhausted():
    outcome = AdaptationAgent(mode="demo", threshold=9.5, max_steps=1).run(sample_screenplay())

    assert outcome.result.status == "budget_exhausted"
    assert outcome.result.steps_used == 1


def test_agent_rejects_unknown_mode():
    with pytest.raises(ValueError, match="不支持的 Agent 模式"):
        AdaptationAgent(mode="magic")


# ---------------------------------------------------------------- ai 模式


def test_ai_agent_follows_scripted_plan(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    screenplay = sample_screenplay()
    rewritten_scene = screenplay.scenes[0].model_dump(mode="json")
    rewritten_scene["elements"] = rewritten_scene["elements"] + [
        {"type": "action", "text": "林夏猛地合上笔记本。"}
    ]

    decisions = [
        {"thought": "先看整体分数", "action": {"tool": "review_screenplay", "params": {}}},
        {
            "thought": "修最弱的 scene-1",
            "action": {
                "tool": "rewrite_scene",
                "params": {
                    "scene_id": "scene-1",
                    "operation": "strengthen_conflict",
                    "feedback": "加强对抗",
                },
            },
        },
        {"thought": "收工", "action": {"tool": "finish", "params": {"summary": "处理完毕"}}},
    ]
    planner_prompts: list[str] = []
    rewrite_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if AGENT_PROMPT_MARKER in prompt:
            planner_prompts.append(prompt)
            return ai_response(decisions[len(planner_prompts) - 1])
        if REVIEW_PROMPT_MARKER in prompt:
            return ai_response(review_payload(6.0, "fail"))
        rewrite_prompts.append(prompt)
        return ai_response(rewritten_scene)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    agent = AdaptationAgent(mode="ai", client=client, threshold=7.0, max_steps=6)
    outcome = agent.run(screenplay, goal="按建议修复")

    result = outcome.result
    assert result.status == "completed"
    assert result.message == "处理完毕"
    assert result.llm_calls == 3
    assert [step.action.tool for step in result.trace if step.action] == [
        "review_screenplay",
        "rewrite_scene",
        "finish",
    ]
    assert rewrite_prompts and "加强对抗" in rewrite_prompts[0]
    assert "按建议修复" in planner_prompts[0]
    assert any("rewrite_scene" in prompt for prompt in planner_prompts[2:])


def test_ai_agent_self_corrects_after_invalid_tool(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    decisions = [
        {"thought": "试试不存在的工具", "action": {"tool": "magic_fix", "params": {}}},
        {"thought": "改用结束", "action": {"tool": "finish", "params": {"summary": "结束"}}},
    ]
    planner_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if AGENT_PROMPT_MARKER in prompt:
            planner_prompts.append(prompt)
            return ai_response(decisions[len(planner_prompts) - 1])
        if REVIEW_PROMPT_MARKER in prompt:
            return ai_response(review_payload(6.0, "fail"))
        raise AssertionError("不应触发重写请求")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = AdaptationAgent(mode="ai", client=client, threshold=7.0, max_steps=6).run(
        sample_screenplay()
    )

    result = outcome.result
    assert result.status == "completed"
    assert result.trace[0].error.startswith("不支持的工具")
    assert "不支持的工具" in planner_prompts[1]


def test_ai_agent_tolerates_non_json_planner_output(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)
    calls = {"planner": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if AGENT_PROMPT_MARKER in prompt:
            calls["planner"] += 1
            if calls["planner"] == 1:
                return text_response("这不是 JSON")
            return ai_response(
                {"thought": "结束", "action": {"tool": "finish", "params": {"summary": "好了"}}}
            )
        if REVIEW_PROMPT_MARKER in prompt:
            return ai_response(review_payload(6.0, "fail"))
        raise AssertionError("不应触发重写请求")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = AdaptationAgent(mode="ai", client=client, threshold=7.0, max_steps=6).run(
        sample_screenplay()
    )

    assert outcome.result.status == "completed"
    assert "无法解析" in outcome.result.trace[0].error


def test_ai_agent_fails_after_consecutive_errors(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if AGENT_PROMPT_MARKER in prompt:
            return ai_response({"thought": "坚持", "action": {"tool": "magic_fix", "params": {}}})
        if REVIEW_PROMPT_MARKER in prompt:
            return ai_response(review_payload(6.0, "fail"))
        raise AssertionError("不应触发重写请求")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = AdaptationAgent(mode="ai", client=client, threshold=7.0, max_steps=10).run(
        sample_screenplay()
    )

    assert outcome.result.status == "failed"
    assert outcome.result.steps_used == 3
    assert "连续 3 次无效动作" in outcome.result.message


# ---------------------------------------------------------------- 记忆


def test_scratchpad_stays_bounded_and_keeps_recent_steps():
    pad = Scratchpad(max_chars=2000)
    for index in range(1, 21):
        pad.append(
            AgentStep(
                step=index,
                thought=f"第 {index} 步思考" + "细节" * 30,
                action=AgentAction(
                    tool="rewrite_scene",
                    params={"scene_id": f"scene-{index}", "operation": "reduce_narration"},
                ),
                observation={"message": "已减少本场旁白式描述。" * 5},
            )
        )

    rendered = pad.render()
    assert len(rendered) <= 2000
    assert "step20" in rendered
    assert "第 20 步思考" in rendered
    assert "第 3 步思考" not in rendered


def test_session_store_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    screenplay = sample_screenplay()
    store = AgentSessionStore()

    outcome = AdaptationAgent(mode="demo", threshold=9.5, max_steps=3).run(
        screenplay, goal="示例目标", session_store=store
    )
    session_id = outcome.result.session_id
    assert session_id.startswith("ag-")

    loaded = store.load(session_id)
    assert loaded["goal"] == "示例目标"
    assert loaded["result"].steps_used == outcome.result.steps_used
    assert len(loaded["screenplay"].scenes) == len(outcome.screenplay.scenes)
    assert loaded["report"] is not None

    sessions = store.list_sessions()
    assert [item["session_id"] for item in sessions] == [session_id]

    with pytest.raises(ValueError, match="会话不存在"):
        store.load("ag-missing")


def test_toolbox_registers_search_story_context_only_with_knowledge():
    from story2script.agent import AgentContext, build_toolbox
    from story2script.rag import build_story_knowledge

    screenplay = sample_screenplay()
    knowledge = build_story_knowledge(parse_chapters(NOVEL))
    ctx = AgentContext(screenplay=screenplay, knowledge=knowledge)
    toolbox = build_toolbox(ctx)

    assert "search_story_context" in toolbox.describe()
    observation = toolbox.execute(
        AgentAction(tool="search_story_context", params={"query": "林夏 出发"})
    )
    assert observation["retriever"] == "lexical"
    assert observation["hits"]
    assert all("snippet" in hit for hit in observation["hits"])

    bare_ctx = AgentContext(screenplay=screenplay)
    assert "search_story_context" not in build_toolbox(bare_ctx).describe()


def test_agent_run_accepts_knowledge():
    from story2script.rag import build_story_knowledge

    knowledge = build_story_knowledge(parse_chapters(NOVEL))
    agent = AdaptationAgent(mode="demo", threshold=0.1)

    outcome = agent.run(sample_screenplay(), knowledge=knowledge)

    assert outcome.result.status == "completed"
