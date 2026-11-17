import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import story2script.job_store as module
from story2script.job_store import DurableJobStore, JobConflictError, JobStorageError, list_jobs
from story2script.main import app
from story2script.job_lock import JobFileLock
from test_job_store import wait_for


class Request(BaseModel):
    value: int = 1


class Result(BaseModel):
    value: int


class Status(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    stage: str
    message: str
    result: Result | None = None
    error: str = ""


class Store(DurableJobStore):
    kind = "reliability"
    request_model = Request
    result_model = Result
    response_model = Status

    def _run(self, job_id):
        self._complete(job_id, Result(value=self._request_for(job_id).value))


class IdleStore(Store):
    def _execute(self, job_id):
        pass


def test_lock_opening_does_not_write_into_another_owner_locked_byte(tmp_path):
    first = JobFileLock(tmp_path / "jobs.db", "same")
    assert first.acquire()
    second = JobFileLock(tmp_path / "jobs.db", "same")
    try:
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        first.close()
        second.close()


def test_unwritable_storage_never_schedules_memory_only_job(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("file")
    store = Store(db_path=blocker / "jobs.db")
    try:
        with pytest.raises(JobStorageError):
            store.create(Request())
        assert not store._rows and not store._scheduled
    finally:
        store._executor.shutdown(wait=True)


def test_http_storage_failure_is_503_without_leaking_db_details(monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")
    monkeypatch.setenv("STORY2SCRIPT_JOBS_DB", str(blocker / "jobs.db"))
    result = TestClient(app).post(
        "/api/convert/jobs", json={"novel_text": "第一章\n甲。\n第二章\n乙。\n第三章\n丙。"}
    )
    assert result.status_code == 503
    assert "存储" in result.json()["detail"] and str(blocker) not in result.text


def test_failed_update_or_completion_never_changes_public_state(tmp_path, monkeypatch):
    store = IdleStore(db_path=tmp_path / "jobs.db")
    job_id = store.create(Request()).job_id
    store._claim(job_id)
    _, _ = store.subscribe(job_id)
    original = store._persist

    def fail(row):
        raise JobStorageError("injected failure")

    monkeypatch.setattr(store, "_persist", fail)
    try:
        with pytest.raises(JobStorageError):
            store._update(job_id, progress=70, stage="unsaved")
        assert store._rows[job_id]["progress"] == 0
        with pytest.raises(JobStorageError):
            store._complete(job_id, Result(value=4))
        snapshot = store.snapshot(job_id)
        assert snapshot.status == "running" and snapshot.result is None
        assert not any(e.get("status") == "succeeded" for e in store._events.get(job_id, []))
        monkeypatch.setattr(store, "_persist", original)
        store._complete(job_id, Result(value=4))
        assert store.snapshot(job_id).result.value == 4
    finally:
        store._executor.shutdown(wait=True)


def test_failed_create_commit_leaves_no_job(monkeypatch, tmp_path):
    path = tmp_path / "jobs.db"
    store = IdleStore(db_path=path)
    conn = module._connect(path)
    conn.execute(
        "CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'denied'); END"
    )
    conn.close()
    with pytest.raises(JobStorageError):
        store.create(Request())
    assert list_jobs(db_path=path) == []
    assert not store._rows and not store._scheduled
    store._executor.shutdown(wait=True)


def test_idempotent_concurrent_submissions_use_one_job(tmp_path):
    store = Store(db_path=tmp_path / "jobs.db")
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(
                pool.map(
                    lambda _: store.create(Request(), idempotency_key="same-click").job_id,
                    range(12),
                )
            )
        assert len(set(ids)) == 1
        assert wait_for(store, ids[0]).status == "succeeded"
        assert store._row(ids[0])["attempts"] == 1
        with pytest.raises(JobConflictError):
            store.create(Request(value=2), idempotency_key="same-click")
        assert store.find_request("same-click").job_id == ids[0]
    finally:
        store._executor.shutdown(wait=True)


def test_second_store_cannot_recover_live_job_twice(tmp_path):
    started, release = threading.Event(), threading.Event()
    executions = []

    class BlockingStore(Store):
        def _run(self, job_id):
            executions.append(job_id)
            started.set()
            assert release.wait(10)
            super()._run(job_id)

    first = BlockingStore(db_path=tmp_path / "jobs.db")
    job_id = first.create(Request()).job_id
    assert started.wait(10)
    second = BlockingStore(db_path=tmp_path / "jobs.db")
    try:
        assert second.snapshot(job_id).status == "running"
        assert second._row(job_id)["attempts"] == 1
        release.set()
        first._executor.shutdown(wait=True)
        second._executor.shutdown(wait=True)
        assert executions == [job_id]
        assert second.snapshot(job_id).status == "succeeded"
    finally:
        release.set()
        first._executor.shutdown(wait=True)
        second._executor.shutdown(wait=True)


def test_process_death_releases_lock_and_recovers_durable_job(tmp_path):
    path = tmp_path / "jobs.db"
    code = """import os
from pathlib import Path
from test_job_reliability import Store, Request
class CrashStore(Store):
    def _run(self, job_id):
        Path(os.environ['TEST_JOB_ID_FILE']).write_text(job_id)
        os._exit(17)
s = CrashStore(db_path=os.environ['TEST_TASK_DB'])
s.create(Request(value=19))
s._executor.shutdown(wait=True)
"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(Path("src").resolve()), str(Path("tests").resolve())]),
        "TEST_TASK_DB": str(path),
        "TEST_JOB_ID_FILE": str(tmp_path / "id"),
        "STORY2SCRIPT_JOBS_DB": str(tmp_path / "unrelated.db"),
        "STORY2SCRIPT_DISABLE_DOTENV": "1",
    }
    crashed = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=30)
    assert crashed.returncode == 17, crashed.stderr.decode(errors="replace")
    recovered = Store(db_path=path)
    job_id = (tmp_path / "id").read_text()
    try:
        assert wait_for(recovered, job_id).result.value == 19
        assert recovered._row(job_id)["attempts"] == 2
    finally:
        recovered._executor.shutdown(wait=True)


def test_stale_status_update_cannot_overwrite_success(tmp_path):
    store = Store(db_path=tmp_path / "jobs.db")
    job_id = store.create(Request()).job_id
    wait_for(store, job_id)
    with pytest.raises(JobConflictError):
        store._update(job_id, progress=20, stage="late")
    store._fail(job_id, "late failure")
    assert store.snapshot(job_id).status == "succeeded"
    store._executor.shutdown(wait=True)


def test_old_database_migrates_without_losing_results(tmp_path):
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY,kind TEXT,status TEXT,progress INTEGER,stage TEXT,message TEXT,error TEXT,request_json TEXT,result_json TEXT,attempts INTEGER,created_at TEXT,updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old",
                "reliability",
                "succeeded",
                100,
                "done",
                "done",
                "",
                '{"value":3}',
                '{"value":3}',
                1,
                "2026-01-01",
                "2026-01-01",
            ),
        )
    store = Store(db_path=path)
    try:
        assert store.snapshot("old").result.value == 3
        assert {"revision", "idempotency_key"} <= set(store._row("old"))
    finally:
        store._executor.shutdown(wait=True)


def test_corrupted_completed_result_is_not_reported_as_success(tmp_path):
    store = Store(db_path=tmp_path / "jobs.db")
    job_id = store.create(Request()).job_id
    wait_for(store, job_id)
    with sqlite3.connect(store._db_path()) as conn:
        conn.execute("UPDATE jobs SET result_json='{broken' WHERE job_id=?", (job_id,))
    with pytest.raises(JobStorageError, match="损坏"):
        store.snapshot(job_id)
    store._executor.shutdown(wait=True)


def test_storage_recovery_after_write_failure_never_claims_saved_success(tmp_path, monkeypatch):
    store = Store(db_path=tmp_path / "jobs.db")
    real = store._persist

    def fail_success(row):
        if row["status"] == "succeeded":
            raise JobStorageError("disk full")
        return real(row)

    monkeypatch.setattr(store, "_persist", fail_success)
    job_id = store.create(Request()).job_id
    assert wait_for(store, job_id).status == "failed"
    monkeypatch.setattr(store, "_persist", real)
    store.resume(job_id)
    assert wait_for(store, job_id).status == "succeeded"
    store._executor.shutdown(wait=True)


def test_job_db_path_is_frozen_for_worker_when_environment_changes(tmp_path, monkeypatch):
    first_path, second_path = tmp_path / "first.db", tmp_path / "second.db"
    monkeypatch.setenv("STORY2SCRIPT_JOBS_DB", str(first_path))
    store = IdleStore()
    job_id = store.create(Request()).job_id
    monkeypatch.setenv("STORY2SCRIPT_JOBS_DB", str(second_path))
    assert store.snapshot(job_id).status == "queued"
    assert not second_path.exists()
    store._executor.shutdown(wait=True)


def test_resume_while_previous_thread_is_finishing_does_not_lose_dispatch(tmp_path):
    failed, release = threading.Event(), threading.Event()

    class FinishingStore(Store):
        def _run(self, job_id):
            if self._row(job_id)["attempts"] == 1:
                self._fail(job_id, "first attempt interrupted")
                failed.set()
                assert release.wait(10)
            else:
                super()._run(job_id)

    store = FinishingStore(db_path=tmp_path / "jobs.db")
    try:
        job_id = store.create(Request()).job_id
        assert failed.wait(10)
        assert store.resume(job_id).status == "queued"
        release.set()
        assert wait_for(store, job_id).status == "succeeded"
        assert store._row(job_id)["attempts"] == 2
    finally:
        release.set()
        store._executor.shutdown(wait=True)
