import sqlite3
import time

from fastapi.testclient import TestClient

from story2script.conversion_jobs import ConversionJobStore
from story2script.converter import DemoConverter
from story2script.api_models import ConvertRequest
from story2script.job_store import list_jobs, resolve_db_path
from story2script.main import app
from story2script.parser import parse_chapters
from story2script.yaml_export import screenplay_to_yaml

client = TestClient(app)

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"


def sample_yaml_text() -> str:
    chapters = parse_chapters(NOVEL)
    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")
    return screenplay_to_yaml(screenplay)


def wait_for(store, job_id: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = store.snapshot(job_id)
        if snapshot.status in {"succeeded", "failed"}:
            return snapshot
        time.sleep(0.05)
    raise AssertionError("任务超时未完成")


def demo_request() -> ConvertRequest:
    return ConvertRequest(novel_text=NOVEL, title="测试故事", genre="剧情", mode="demo")


# ---------------------------------------------------------------- 持久化


def test_job_survives_across_store_instances():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    snapshot = wait_for(store, job_id)
    assert snapshot.status == "succeeded"

    reopened = ConversionJobStore()  # 模拟新进程：同一个 DB 文件
    assert reopened.has_job(job_id)
    restored = reopened.snapshot(job_id)
    assert restored.status == "succeeded"
    assert restored.stage == "转换完成"
    assert restored.result is not None
    assert restored.result.screenplay.scenes


def test_running_job_requeued_on_restart():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    wait_for(store, job_id)

    # 模拟进程在执行中被杀：把行改回 running 后重建 store
    with sqlite3.connect(resolve_db_path()) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', result_json = '', attempts = 1 "
            "WHERE job_id = ?",
            (job_id,),
        )

    recovered = ConversionJobStore()
    snapshot = wait_for(recovered, job_id)
    assert snapshot.status == "succeeded"
    assert snapshot.result is not None


def test_running_job_fails_after_restart_attempt_limit():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    wait_for(store, job_id)

    with sqlite3.connect(resolve_db_path()) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', result_json = '', attempts = 3 "
            "WHERE job_id = ?",
            (job_id,),
        )

    recovered = ConversionJobStore()
    snapshot = recovered.snapshot(job_id)
    assert snapshot.status == "failed"
    assert "进程重启中断" in snapshot.error


def test_corrupt_request_row_marked_failed_on_recover():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    wait_for(store, job_id)

    with sqlite3.connect(resolve_db_path()) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', request_json = '{broken' WHERE job_id = ?",
            (job_id,),
        )

    recovered = ConversionJobStore()
    snapshot = recovered.snapshot(job_id)
    assert snapshot.status == "failed"
    assert "任务数据损坏" in snapshot.error


# ---------------------------------------------------------------- 取消


def test_cancel_only_applies_to_queued_jobs():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    snapshot = wait_for(store, job_id)
    assert snapshot.status == "succeeded"

    try:
        store.cancel(job_id)
        raise AssertionError("已完成任务不应可取消")
    except ValueError as exc:
        assert "无法取消" in str(exc)


def test_cancelled_queued_job_never_runs(monkeypatch):
    executed = []

    class SlowStore(ConversionJobStore):
        def _execute(self, job_id: str) -> None:
            time.sleep(0.3)  # 给取消留出窗口
            if self._claim(job_id):
                executed.append(job_id)
                self._run(job_id)

    store = SlowStore()
    job_id = store.create(demo_request()).job_id
    store.cancel(job_id)

    snapshot = store.snapshot(job_id)
    assert snapshot.status == "failed"
    assert snapshot.stage == "已取消"

    time.sleep(0.5)
    assert executed == []  # claim 失败，业务逻辑从未执行


# ---------------------------------------------------------------- 列表与 API


def test_list_jobs_returns_light_rows():
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    wait_for(store, job_id)

    rows = list_jobs(limit=10)
    assert rows
    row = next(item for item in rows if item["job_id"] == job_id)
    assert row["kind"] == "convert"
    assert row["status"] == "succeeded"
    assert "request_json" not in row
    assert "result_json" not in row


def test_jobs_api_and_cancel_routes():
    start = client.post(
        "/api/convert/jobs",
        json={"novel_text": NOVEL, "title": "测试故事", "mode": "demo"},
    )
    job_id = start.json()["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        payload = client.get(f"/api/convert/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    assert payload["status"] == "succeeded"

    listing = client.get("/api/jobs?limit=5")
    assert listing.status_code == 200
    assert any(item["job_id"] == job_id for item in listing.json()["jobs"])

    finished_cancel = client.post(f"/api/convert/jobs/{job_id}/cancel")
    assert finished_cancel.status_code == 422
    assert "无法取消" in finished_cancel.json()["detail"]

    missing_cancel = client.post("/api/convert/jobs/not-exist/cancel")
    assert missing_cancel.status_code == 404

    agent_missing = client.post("/api/agent/runs/not-exist/cancel")
    assert agent_missing.status_code == 404
