"""Real Redis + independent Celery process. Opt in with STORY2SCRIPT_TEST_REDIS_URL."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

URL = os.getenv("STORY2SCRIPT_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="real Redis integration endpoint not configured")


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    pytest.importorskip("redis")
    pytest.importorskip("celery")
    prefix = "test-" + uuid4().hex
    monkeypatch.setenv("STORY2SCRIPT_REDIS_URL", URL)
    monkeypatch.setenv("STORY2SCRIPT_REDIS_NAMESPACE", prefix)
    monkeypatch.setenv("STORY2SCRIPT_TASK_BACKEND", "redis")
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    monkeypatch.setenv("STORY2SCRIPT_REDIS_DISPATCH_SECONDS", "5")
    from story2script.redis_runtime import redis_client

    client = redis_client()
    client.ping()
    processes, streams = [], []

    def start(script=None):
        env = {
            **os.environ,
            "PYTHONPATH": str(Path("src").resolve()),
            "STORY2SCRIPT_DISABLE_DOTENV": "1",
        }
        log = (tmp_path / f"worker-{len(processes)}.log").open("w", encoding="utf-8")
        streams.append(log)
        command = (
            [sys.executable, "-m", "story2script.worker", "--concurrency", "2"]
            if script is None
            else [sys.executable, str(script)]
        )
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    yield client, start, tmp_path
    for process in processes:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for log in streams:
        log.close()
    # Only this test's random namespace; never FLUSHDB or touch other applications.
    keys = list(client.scan_iter(match=prefix + "*"))
    if keys:
        client.delete(*keys)


def wait_result(store, job_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = store.snapshot(job_id)
        if result.status in ("succeeded", "failed"):
            assert result.status == "succeeded", result.error
            return result
        time.sleep(0.2)
    raise AssertionError(f"Worker did not finish job {job_id}")


def test_real_broker_worker_database_and_stream(deployment):
    client, start, _ = deployment
    from story2script.api_models import ConvertRequest
    from story2script.conversion_jobs import ConversionJobStore
    from story2script.redis_runtime import read_events

    store = ConversionJobStore()
    try:
        job = store.create(
            ConvertRequest(
                novel_text="第一章\n林夏说：出发吧。\n第二章\n周宁站在码头。\n第三章\n林夏回到家里。",
                mode="demo",
            ),
            idempotency_key="real-task",
        )
        assert job.status == "queued" and not store._active
        start()
        result = wait_result(store, job.job_id)
        assert result.result.screenplay.scenes
        assert store._row(job.job_id)["attempts"] == 1
        events = read_events(job.job_id + ":0:1")
        assert any(e["type"] == "scene" for _, e in events)
        assert events[-1][1]["type"] == "done"
        # Redelivery after completion cannot regenerate or overwrite the result.
        from story2script.celery_app import get_celery_app
        from story2script.redis_dispatch import database_identity

        get_celery_app().send_task(
            "story2script.execute_job", args=[store.kind, job.job_id, 0, database_identity()]
        )
        assert store.snapshot(job.job_id).result == result.result
    finally:
        store._executor.shutdown(wait=True)


def test_real_worker_repairs_unpublished_database_job(deployment, monkeypatch):
    _, start, _ = deployment
    from story2script.api_models import ConvertRequest
    from story2script.conversion_jobs import ConversionJobStore

    store = ConversionJobStore()
    monkeypatch.setattr(store, "_enqueue", lambda *a, **k: None)
    try:
        job_id = store.create(
            ConvertRequest(
                novel_text="第一章\n甲说：你好。\n第二章\n乙走了。\n第三章\n甲回家。", mode="demo"
            )
        ).job_id
        start()
        assert wait_result(store, job_id).result.screenplay
    finally:
        store._executor.shutdown(wait=True)


def test_real_cache_and_rate_limit_are_shared_between_processes(deployment, monkeypatch):
    client, _, _ = deployment
    from story2script.llm_cache import LLMResponseCache
    from story2script.redis_runtime import rate_limit

    LLMResponseCache().put("integration-key", {"value": "跨进程缓存"})
    monkeypatch.setenv("STORY2SCRIPT_LLM_RPM", "1")
    rate_limit("provider", "credential", "generation")
    code = """import json
from story2script.llm_cache import LLMResponseCache
from story2script.redis_runtime import rate_limit
value=LLMResponseCache().get('integration-key')
limited=False
try: rate_limit('provider','credential','generation')
except ValueError as exc: limited=exc.status_code==429
print(json.dumps({'cached':value,'limited':limited},ensure_ascii=True))
"""
    response = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert response.returncode == 0, response.stderr
    data = json.loads(response.stdout)
    assert data["cached"] == {"value": "跨进程缓存"} and data["limited"]


def test_real_worker_crash_restores_saved_studio_response(deployment):
    _, start, root = deployment
    from test_plot_revision import completed_revision
    from story2script.creative_models import EditingState
    from story2script.plot_state import screenplay_hash
    from story2script.studio_models import StudioJobRequest
    from story2script.studio_jobs import StudioJobStore
    from story2script.yaml_export import screenplay_to_yaml

    _, revision, _ = completed_revision()
    script = revision.screenplay
    script.editing = EditingState(
        based_on_hash=screenplay_hash(script), dirty_scene_ids=["scene-3", "scene-4"]
    )
    (root / "script.json").write_text(script.model_dump_json(), encoding="utf-8")
    (root / "worker_fixture.py").write_text(
        """import json,os,time
from pathlib import Path
from types import SimpleNamespace
root=Path(__file__).parent
from story2script.screenplay import Screenplay
script=Screenplay.model_validate_json((root/'script.json').read_text(encoding='utf-8'))
import story2script.studio_jobs as jobs
class Fixture:
    timeout_seconds=30
    def __init__(self,**kw): self.client=SimpleNamespace(close=lambda:None)
    def complete_json(self,prompt,*,use_cache,prompt_id):
        ctx=json.loads(prompt.split('上下文 JSON：',1)[1]);sid=ctx.get('scene',{}).get('id','')
        with (root/'calls.log').open('a') as log: log.write(prompt_id+' '+sid+'\\n')
        if sid=='scene-4' and not (root/'crashed').exists():
            (root/'crashed').write_text('once');os._exit(17)
        if prompt_id=='studio.scene_recheck':
            scene=next(s for s in script.plot_state.logic.scenes if s.scene_id==sid)
            return json.dumps({'scene_id':sid,'issues':[],'facts':[],'logic':scene.model_dump(mode='json')})
        return json.dumps({'scanned_scene_ids':[s.id for s in script.scenes],'issues':[]})
jobs.LLMClient=Fixture
from story2script.worker import main
main(['--concurrency','1'])
""",
        encoding="utf-8",
    )
    store = StudioJobStore()
    try:
        req = StudioJobRequest(
            operation="recheck", parameters={"yaml_text": screenplay_to_yaml(script), "mode": "ai"}
        )
        job = store.create(req)
        first = start(root / "worker_fixture.py")
        assert first.wait(timeout=40) == 17
        start(root / "worker_fixture.py")
        result = wait_result(store, job.job_id, timeout=45)
        assert result.result.payload["valid"] is True and result.attempts == 2
        calls = (root / "calls.log").read_text().splitlines()
        assert calls.count("studio.scene_recheck scene-3") == 1
        assert calls.count("studio.scene_recheck scene-4") == 2
        assert result.calls == 4
    finally:
        store._executor.shutdown(wait=True)
