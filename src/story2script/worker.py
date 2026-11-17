"""Run: python -m story2script.worker. Uses threads on Windows and POSIX alike."""

import argparse
import logging
import os
import threading
from uuid import uuid4


def main(argv=None):
    parser = argparse.ArgumentParser(description="Story2Script Redis 后台 Worker")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--loglevel", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 32:
        parser.error("concurrency 必须在 1 到 32 之间")
    os.environ["STORY2SCRIPT_TASK_BACKEND"] = "redis"
    from .redis_runtime import redis_client, namespace

    try:
        redis_client().ping()
    except Exception:
        parser.exit(2, "Redis 不可连接，请先启动服务并检查 STORY2SCRIPT_REDIS_URL。\n")
    from .celery_app import get_celery_app
    from .redis_dispatch import dispatch_pending, heartbeat
    from .redis_tasks import job_stores

    job_stores()  # Reconcile old queued/running rows into the durable outbox.
    worker_id, stop = str(uuid4()), threading.Event()

    def repair():
        while not stop.is_set():
            try:
                heartbeat(worker_id)
                dispatch_pending()
            except Exception:
                logging.getLogger(__name__).warning("Redis/数据库暂不可用，Worker 将重试补投。")
            stop.wait(5)

    thread = threading.Thread(target=repair, daemon=True, name="outbox-dispatcher")
    thread.start()
    try:
        get_celery_app().worker_main(
            [
                "worker",
                "--pool=threads",
                f"--concurrency={args.concurrency}",
                f"--loglevel={args.loglevel}",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                f"--hostname={namespace()}-{worker_id[:8]}",
            ]
        )
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
