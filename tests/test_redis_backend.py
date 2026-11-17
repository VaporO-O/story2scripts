import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import story2script.redis_dispatch as dispatch
import story2script.redis_runtime as runtime
from story2script.job_store import _connect
from story2script.llm_cache import LLMResponseCache
from story2script.main import app
from test_job_reliability import Request, Store

fakeredis = pytest.importorskip("fakeredis")
pytest.importorskip("celery")


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(runtime, "redis_client", lambda: client)
    monkeypatch.setattr(dispatch, "redis_client", lambda: client)
    monkeypatch.setenv("STORY2SCRIPT_REDIS_NAMESPACE", "unit-test")
    yield client
    client.close()


@pytest.fixture
def fake_broker(monkeypatch, fake_redis):
    from story2script import celery_app

    deliveries = []
    monkeypatch.setattr(
        celery_app,
        "get_celery_app",
        lambda: SimpleNamespace(send_task=lambda *a, **k: deliveries.append((a, k))),
    )
    monkeypatch.setenv("STORY2SCRIPT_TASK_BACKEND", "redis")
    return deliveries


def test_redis_mode_queues_ids_without_running_in_web(tmp_path, fake_broker):
    store = Store(db_path=tmp_path / "jobs.db")
    try:
        snapshot = store.create(Request(value=8))
        assert snapshot.status == "queued" and snapshot.result is None
        assert not store._scheduled
        assert len(fake_broker) == 1
        task, arguments = fake_broker[0]
        assert task == ("story2script.execute_job",)
        assert arguments["args"][:3] == ["reliability", snapshot.job_id, 0]
        assert "request_json" not in json.dumps(arguments)
        store._execute(snapshot.job_id, generation=0, wait_for_lock=False)
        assert store.snapshot(snapshot.job_id).result.value == 8
        conn = _connect(store._db_path())
        assert conn.execute("SELECT COUNT(*) FROM job_outbox").fetchone()[0] == 0
        conn.close()
    finally:
        store._executor.shutdown(wait=True)


def test_broker_outage_keeps_durable_job_and_repair_republishes(tmp_path, fake_broker, monkeypatch):
    from story2script import celery_app

    def fail(*args, **kwargs):
        raise ConnectionError("redis://secret:password@private")

    original = celery_app.get_celery_app
    monkeypatch.setattr(celery_app, "get_celery_app", lambda: SimpleNamespace(send_task=fail))
    store = Store(db_path=tmp_path / "jobs.db")
    try:
        job = store.create(Request(), idempotency_key="click")
        assert job.status == "queued" and "补投" in job.message
        assert "password" not in job.message
        monkeypatch.setattr(celery_app, "get_celery_app", original)
        with _connect(store._db_path()) as conn:
            conn.execute("UPDATE job_outbox SET next_send=0")
        assert dispatch.dispatch_pending(store._db_path()) == 1
        assert store.create(Request(), idempotency_key="click").job_id == job.job_id
        assert len(fake_broker) == 1
    finally:
        store._executor.shutdown(wait=True)


def test_duplicate_delivery_and_old_generation_never_rerun_result(tmp_path, fake_broker):
    calls = []

    class CountingStore(Store):
        def _run(self, job_id):
            calls.append(job_id)
            if len(calls) == 1:
                self._fail(job_id, "temporary")
            else:
                super()._run(job_id)

    store = CountingStore(db_path=tmp_path / "jobs.db")
    try:
        job_id = store.create(Request()).job_id
        store._execute(job_id, generation=0, wait_for_lock=False)
        assert store.snapshot(job_id).status == "failed"
        store.resume(job_id)
        store._execute(job_id, generation=0, wait_for_lock=False)
        assert len(calls) == 1 and store.snapshot(job_id).status == "queued"
        store._execute(job_id, generation=1, wait_for_lock=False)
        store._execute(job_id, generation=1, wait_for_lock=False)
        assert len(calls) == 2 and store.snapshot(job_id).status == "succeeded"
    finally:
        store._executor.shutdown(wait=True)


def test_repair_after_missing_delivery_preserves_live_execution(tmp_path, fake_broker):
    started, release = threading.Event(), threading.Event()
    calls = []

    class LiveStore(Store):
        def _run(self, job_id):
            calls.append(job_id)
            started.set()
            assert release.wait(10)
            super()._run(job_id)

    store = LiveStore(db_path=tmp_path / "jobs.db")
    job_id = store.create(Request()).job_id
    thread = threading.Thread(
        target=lambda: store._execute(job_id, generation=0, wait_for_lock=False)
    )
    thread.start()
    try:
        assert started.wait(10)
        store._execute(job_id, generation=0, wait_for_lock=False)
        assert job_id in store._active and len(calls) == 1
        with _connect(store._db_path()) as conn:
            conn.execute("UPDATE job_outbox SET next_send=0")
        assert dispatch.dispatch_pending(store._db_path()) == 1
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(10)
        store._executor.shutdown(wait=True)


def test_redis_cache_shared_expiry_and_no_local_stale_copy(monkeypatch, fake_redis):
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    monkeypatch.setenv("STORY2SCRIPT_REDIS_CACHE_TTL", "5")
    a, b = LLMResponseCache(), LLMResponseCache()
    a.put("key", [1.0, 2.0])
    assert b.get("key") == [1.0, 2.0]
    assert 0 < fake_redis.ttl("unit-test:cache:v1:key") <= 5
    fake_redis.delete("unit-test:cache:v1:key")
    assert a.get("key") is None and b.get("key") is None
    assert a.stats()["entries"] == 0


def test_cache_namespace_isolation_and_failure_is_only_a_miss(monkeypatch, fake_redis):
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    a = LLMResponseCache()
    a.put("key", "private draft")
    monkeypatch.setenv("STORY2SCRIPT_REDIS_NAMESPACE", "another-project")
    assert a.get("key") is None

    def broken():
        raise ConnectionError("secret")

    monkeypatch.setattr(runtime, "redis_client", broken)
    assert a.get("key") is None
    a.put("key", "allowed uncached result")


def test_disabled_cache_and_oversized_value_are_not_written(monkeypatch, fake_redis):
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    monkeypatch.setenv("STORY2SCRIPT_REDIS_CACHE_MAX_BYTES", "10")
    cache = LLMResponseCache()
    cache.put("big", "x" * 20)
    assert fake_redis.dbsize() == 0
    monkeypatch.setenv("STORY2SCRIPT_LLM_CACHE_DISABLE", "1")
    cache.put("small", "ok")
    assert fake_redis.dbsize() == 0


def test_atomic_global_limit_across_concurrent_clients(monkeypatch, fake_redis):
    monkeypatch.setenv("STORY2SCRIPT_LLM_RPM", "4")

    def request(_):
        try:
            runtime.rate_limit("https://provider/v1", "credential", "generation")
            return True
        except ValueError as exc:
            assert exc.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(request, range(16))) == 4
    runtime.rate_limit("https://provider/v1", "other-account", "generation")
    assert not any("credential" in key for key in fake_redis.scan_iter())


def test_rate_outage_fails_closed_and_does_not_expose_url(monkeypatch):
    monkeypatch.setenv("STORY2SCRIPT_LLM_RPM", "10")

    def broken():
        raise ConnectionError("redis://private:secret@example.test")

    monkeypatch.setattr(runtime, "redis_client", broken)
    with pytest.raises(ValueError, match="请求未发出") as exc:
        runtime.rate_limit("provider", "key", "generation")
    assert "secret" not in str(exc.value) and exc.value.status_code == 503


def test_rate_window_resets_and_embedding_quota_is_separate(monkeypatch, fake_redis):
    monkeypatch.setenv("STORY2SCRIPT_LLM_RPM", "1")
    monkeypatch.setenv("STORY2SCRIPT_EMBED_RPM", "1")
    runtime.rate_limit("provider", "account", "generation")
    runtime.rate_limit("provider", "account", "embedding")
    keys = list(fake_redis.scan_iter(match="unit-test:rate:*"))
    assert len(keys) == 2
    for key in keys:
        fake_redis.hset(key, "start", "0")
    runtime.rate_limit("provider", "account", "generation")
    runtime.rate_limit("provider", "account", "embedding")


def test_cached_calls_skip_global_quota_and_regeneration_does_not(monkeypatch, fake_redis):
    from test_llm_cache import configure_ai, chat_response
    from story2script.llm_client import LLMClient

    configure_ai(monkeypatch)
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    monkeypatch.setenv("STORY2SCRIPT_LLM_RPM", "1")
    calls = []

    def handler(request):
        calls.append(request)
        return chat_response({"ok": True})

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    first = client.complete_json("same prompt")
    assert client.complete_json("same prompt") == first
    with pytest.raises(ValueError, match="共享模型"):
        client.complete_json("same prompt", use_cache=False)
    assert len(calls) == 1


def test_cache_keys_isolate_provider_accounts_and_prompt_versions(monkeypatch, fake_redis):
    from test_llm_cache import configure_ai, chat_response
    from story2script.llm_client import LLMClient

    configure_ai(monkeypatch)
    monkeypatch.setenv("STORY2SCRIPT_CACHE_BACKEND", "redis")
    calls = []

    def handler(request):
        calls.append(1)
        return chat_response({"n": len(calls)})

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.complete_json("prompt", prompt_id="v1")
    client.complete_json("prompt", prompt_id="v2")
    monkeypatch.setenv("AI_API_KEY", "different-key")
    client.complete_json("prompt", prompt_id="v2")
    assert len(calls) == 3


def test_stream_replay_is_ordered_bounded_and_can_be_read_by_another_client(
    fake_redis, monkeypatch
):
    monkeypatch.setenv("STORY2SCRIPT_REDIS_EVENT_LIMIT", "3")
    for i in range(5):
        runtime.append_event("job:0:1", {"type": "progress", "progress": i})
    replay = runtime.read_events("job:0:1")
    assert [e["progress"] for _, e in replay] == [2, 3, 4]
    assert runtime.read_events("job:0:1", replay[-1][0]) == []
    assert fake_redis.ttl("unit-test:events:job:0:1") > 0


def test_event_transport_failure_does_not_undo_success(tmp_path, fake_broker, monkeypatch):
    def broken():
        raise ConnectionError("down")

    monkeypatch.setattr(runtime, "redis_client", broken)
    store = Store(db_path=tmp_path / "jobs.db")
    try:
        job_id = store.create(Request()).job_id
        store._execute(job_id, generation=0, wait_for_lock=False)
        assert store.snapshot(job_id).status == "succeeded"
    finally:
        store._executor.shutdown(wait=True)


def test_infrastructure_status_never_contains_credentials(fake_redis, fake_broker, monkeypatch):
    monkeypatch.setenv("STORY2SCRIPT_REDIS_URL", "redis://admin:secret@127.0.0.1:6379/0")
    dispatch.heartbeat("worker-unit")
    response = TestClient(app).get("/api/infrastructure")
    assert response.status_code == 200
    assert response.json()["workers"] == 1 and response.json()["redis"] == "connected"
    assert "secret" not in response.text and "redis://" not in response.text


def test_queue_task_rejects_wrong_database(fake_broker):
    from story2script.redis_tasks import execute_job

    with pytest.raises(ValueError, match="数据库标识"):
        execute_job.run("convert", "some-job", 0, "wrong-deployment")


def test_tls_broker_requires_certificate_verification():
    import ssl
    from story2script.celery_app import _make_app
    tls_app = _make_app("rediss://127.0.0.1:6379/0", "tls-check", 14400)
    assert tls_app.conf.broker_use_ssl["ssl_cert_reqs"] == ssl.CERT_REQUIRED
