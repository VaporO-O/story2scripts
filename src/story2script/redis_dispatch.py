"""Database outbox repairs the gap between durable creation and Redis publication."""

import logging
import time
from functools import lru_cache
from uuid import uuid4

from .redis_runtime import namespace, number, redis_client, key_for

logger = logging.getLogger(__name__)
OUTBOX_SCHEMA = """CREATE TABLE IF NOT EXISTS job_outbox (
    job_id TEXT NOT NULL, generation INTEGER NOT NULL,
    next_send REAL NOT NULL DEFAULT 0, dispatches INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '', PRIMARY KEY(job_id,generation))"""


@lru_cache(maxsize=8)
def _database_identity(path):
    from .job_store import _connect

    conn = _connect(path)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS job_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO job_metadata VALUES('deployment_id',?)", (str(uuid4()),)
            )
        return conn.execute("SELECT value FROM job_metadata WHERE key='deployment_id'").fetchone()[
            0
        ]
    finally:
        conn.close()


def database_identity(path=None):
    from .job_store import resolve_db_path

    return _database_identity(resolve_db_path(path).resolve())


def dispatch_pending(path=None, job_id=None):
    from .job_store import _connect, resolve_db_path
    from .celery_app import get_celery_app

    path = resolve_db_path(path).absolute()
    retry = number("STORY2SCRIPT_REDIS_DISPATCH_SECONDS", 60, minimum=5, maximum=3600)
    now, conn = time.time(), _connect(path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM job_outbox WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.job_id=job_outbox.job_id AND jobs.generation=job_outbox.generation AND jobs.status IN ('queued','running'))"
            )
        sql = "SELECT o.job_id,o.generation,j.kind FROM job_outbox o JOIN jobs j ON j.job_id=o.job_id WHERE o.next_send<=?"
        params = [now]
        if job_id:
            sql += " AND o.job_id=?"
            params.append(job_id)
        rows = conn.execute(sql + " ORDER BY o.next_send LIMIT 100", params).fetchall()
        delivered = 0
        for jid, generation, kind in rows:
            # One dispatcher reserves a publication window. Process death before send
            # leaves a bounded delay; next dispatcher retries with the same generation.
            with conn:
                claimed = conn.execute(
                    "UPDATE job_outbox SET next_send=?,dispatches=dispatches+1 WHERE job_id=? AND generation=? AND next_send<=?",
                    (now + retry, jid, generation, now),
                ).rowcount
            if not claimed:
                continue
            try:
                get_celery_app().send_task(
                    "story2script.execute_job",
                    args=[kind, jid, generation, database_identity(path)],
                    task_id=f"{namespace()}:{jid}:{generation}",
                    retry=False,
                )
                with conn:
                    conn.execute(
                        "UPDATE job_outbox SET last_error='' WHERE job_id=? AND generation=?",
                        (jid, generation),
                    )
                delivered += 1
            except Exception:
                with conn:
                    conn.execute(
                        "UPDATE job_outbox SET last_error=? WHERE job_id=? AND generation=?",
                        ("Redis 队列暂不可用，任务已保存等待补投。", jid, generation),
                    )
                logger.warning("Redis dispatch unavailable; task remains in durable outbox.")
                break
        return delivered
    finally:
        conn.close()


def heartbeat(worker_id):
    identity = database_identity()
    redis_client().set(key_for("workers", identity + ":" + worker_id), "ready", ex=20)
