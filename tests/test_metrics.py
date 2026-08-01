import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from story2script.agent import AdaptationAgent
from story2script.api_models import ConvertRequest
from story2script.conversion_jobs import ConversionJobStore
from story2script.converter import DemoConverter
from story2script.llm_client import LLMClient
from story2script.main import app
from story2script.metrics import MetricsRegistry, metrics
from story2script.parser import parse_chapters
from story2script.scene_review import review_scenes_report
from story2script.scene_rewrite import rewrite_scene

client = TestClient(app)

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"


def sample_screenplay():
    return DemoConverter().convert(parse_chapters(NOVEL), title="测试故事", genre="剧情")


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


# ---------------------------------------------------------------- 注册表单元


def test_registry_aggregates_llm_calls():
    registry = MetricsRegistry()
    registry.record_llm_call("AI mode", model="m", duration_ms=100, ok=True, prompt_tokens=10, completion_tokens=20)
    registry.record_llm_call("AI mode", model="m", duration_ms=300, ok=False, error_kind="timeout")

    summary = registry.summary()
    row = summary["llm"]["AI mode"]
    assert row["calls"] == 2
    assert row["success"] == 1
    assert row["success_rate"] == 0.5
    assert row["avg_ms"] == 200
    assert row["p50_ms"] in {100, 300}
    assert row["p95_ms"] == 300
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 30
    assert row["last_error"] == "timeout"
    assert summary["llm_overall"]["calls"] == 2


def test_registry_aggregates_tasks_without_token_fields():
    registry = MetricsRegistry()
    registry.record_task("convert", mode="demo", duration_ms=50, ok=True, extra={"scene_count": 3})
    registry.record_task("convert", mode="demo", duration_ms=150, ok=False, error="解析失败")

    summary = registry.summary()
    row = summary["tasks"]["convert"]
    assert row["calls"] == 2
    assert row["success_rate"] == 0.5
    assert row["avg_ms"] == 100
    assert row["last_error"] == "解析失败"
    assert "total_tokens" not in row


def test_registry_ring_buffer_keeps_cumulative_totals(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORY2SCRIPT_METRICS_MAX_EVENTS", "10")
    registry = MetricsRegistry()
    for index in range(50):
        registry.record_llm_call("AI mode", duration_ms=index, ok=True, prompt_tokens=1)

    summary = registry.summary()
    assert summary["llm"]["AI mode"]["calls"] == 50
    assert summary["llm"]["AI mode"]["prompt_tokens"] == 50
    assert len(registry.recent_events(500)) == 10


def test_registry_recent_events_and_reset():
    registry = MetricsRegistry()
    registry.record_task("convert", ok=True)
    registry.record_llm_call("AI mode", ok=True)

    events = registry.recent_events(limit=1)
    assert len(events) == 1
    assert events[0]["type"] == "llm_call"

    registry.reset()
    assert registry.summary()["llm"] == {}
    assert registry.recent_events() == []


def test_registry_thread_safety():
    registry = MetricsRegistry()

    def worker():
        for _ in range(200):
            registry.record_llm_call("AI mode", duration_ms=1, ok=True)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert registry.summary()["llm"]["AI mode"]["calls"] == 1600


def test_registry_jsonl_sink(tmp_path, monkeypatch: pytest.MonkeyPatch):
    log_path = tmp_path / "logs" / "metrics.jsonl"
    monkeypatch.setenv("STORY2SCRIPT_METRICS_LOG", str(log_path))
    registry = MetricsRegistry()
    registry.record_task("convert", mode="demo", ok=True)
    registry.record_llm_call("AI mode", ok=False, error_kind="timeout")

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "task"
    assert json.loads(lines[1])["error_kind"] == "timeout"


# ---------------------------------------------------------------- llm_client 埋点


def ai_client(handler) -> LLMClient:
    return LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_llm_call_success_records_usage(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34},
            },
        )

    ai_client(handler).complete_json("你好")

    row = metrics.summary()["llm"]["AI mode"]
    assert row["calls"] == 1
    assert row["success_rate"] == 1.0
    assert row["prompt_tokens"] == 12
    assert row["completion_tokens"] == 34


def test_llm_call_without_usage_records_zero_tokens(monkeypatch: pytest.MonkeyPatch):
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    ai_client(handler).complete_json("你好")

    row = metrics.summary()["llm"]["AI mode"]
    assert row["total_tokens"] == 0
    assert row["success"] == 1


@pytest.mark.parametrize(
    ("handler_kind", "expected_kind", "match"),
    [
        ("timeout", "timeout", "timed out"),
        ("http_500", "http_status", "HTTP 500"),
        ("truncated", "truncated", "输出被截断"),
    ],
)
def test_llm_call_failures_record_error_kind(
    monkeypatch: pytest.MonkeyPatch, handler_kind: str, expected_kind: str, match: str
):
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if handler_kind == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if handler_kind == "http_500":
            return httpx.Response(500, request=request, json={"error": "boom"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "片段"}, "finish_reason": "length"}]},
        )

    with pytest.raises(ValueError, match=match):
        ai_client(handler).complete_json("你好")

    row = metrics.summary()["llm"]["AI mode"]
    assert row["calls"] == 1
    assert row["failure"] == 1
    assert row["last_error"] == expected_kind


# ---------------------------------------------------------------- 任务级埋点


def test_demo_rewrite_and_review_record_tasks():
    screenplay = sample_screenplay()

    rewrite_scene(screenplay, "scene-1", "strengthen_conflict")
    review_scenes_report(screenplay, mode="demo")

    tasks = metrics.summary()["tasks"]
    assert tasks["scene_rewrite"]["calls"] == 1
    assert tasks["scene_rewrite"]["success_rate"] == 1.0
    assert tasks["scene_review"]["calls"] == 1

    events = metrics.recent_events()
    rewrite_event = next(event for event in events if event.get("kind") == "scene_rewrite")
    assert rewrite_event["extra"]["operation"] == "strengthen_conflict"


def test_rewrite_failure_records_task():
    with pytest.raises(ValueError, match="未找到场景"):
        rewrite_scene(sample_screenplay(), "scene-999", "strengthen_conflict")

    row = metrics.summary()["tasks"]["scene_rewrite"]
    assert row["failure"] == 1
    assert "未找到场景" in row["last_error"]


def test_agent_run_records_task():
    AdaptationAgent(mode="demo", threshold=9.5, max_steps=3).run(sample_screenplay())

    row = metrics.summary()["tasks"]["agent_run"]
    assert row["calls"] == 1
    assert row["success_rate"] == 1.0
    event = next(event for event in metrics.recent_events() if event.get("kind") == "agent_run")
    assert event["extra"]["status"] in {"completed", "budget_exhausted"}
    assert event["extra"]["steps"] > 0


def test_conversion_job_records_task():
    store = ConversionJobStore()
    job = store.create(ConvertRequest(novel_text=NOVEL, mode="demo"))
    for _ in range(200):
        snapshot = store.snapshot(job.job_id)
        if snapshot.status in {"succeeded", "failed"}:
            break
        threading.Event().wait(0.05)

    assert snapshot.status == "succeeded"
    row = metrics.summary()["tasks"]["convert"]
    assert row["calls"] == 1
    event = next(event for event in metrics.recent_events() if event.get("kind") == "convert")
    assert event["extra"]["source"] == "job"
    assert event["extra"]["scene_count"] >= 3


# ---------------------------------------------------------------- API 与集成


def test_sync_convert_api_records_success_and_failure():
    ok = client.post("/api/convert", json={"novel_text": NOVEL, "mode": "demo"})
    assert ok.status_code == 200

    bad = client.post("/api/convert", json={"novel_text": "第一章 只有一章\n内容。", "mode": "demo"})
    assert bad.status_code == 422

    row = metrics.summary()["tasks"]["convert"]
    assert row["calls"] == 2
    assert row["success"] == 1
    assert "章节" in row["last_error"]


def test_metrics_api_summary_and_events():
    client.post("/api/convert", json={"novel_text": NOVEL, "mode": "demo"})

    summary = client.get("/api/metrics")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["tasks"]["convert"]["calls"] == 1
    assert payload["llm"] == {}
    assert payload["generated_at"]

    events = client.get("/api/metrics/events", params={"limit": 1})
    assert events.status_code == 200
    listed = events.json()["events"]
    assert len(listed) == 1
    assert listed[0]["type"] == "task"


def test_registry_tracks_cache_hits_for_llm_rows_only():
    registry = MetricsRegistry()
    registry.record_llm_call("AI mode", duration_ms=100, ok=True)
    registry.record_llm_call("AI mode", duration_ms=0, ok=True, cached=True)
    registry.record_task("convert", duration_ms=10, ok=True)

    summary = registry.summary()
    assert summary["llm"]["AI mode"]["cache_hits"] == 1
    assert summary["llm_overall"]["cache_hits"] == 1
    assert "cache_hits" not in summary["tasks"]["convert"]

    cached_event = next(event for event in registry.recent_events() if event.get("cached"))
    assert cached_event["type"] == "llm_call"
