"""Celery executes IDs only; the database remains the source of truth."""

from functools import lru_cache
import ssl

from .redis_runtime import namespace, number, redis_url


@lru_cache(maxsize=4)
def _make_app(url, prefix, visibility):
    from celery import Celery

    app = Celery("story2script", broker=url, include=["story2script.redis_tasks"])
    app.conf.update(
        task_default_queue=prefix + ".jobs",
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=2,
        broker_transport_options={
            "visibility_timeout": visibility,
            "global_keyprefix": prefix + ":broker:",
            "socket_connect_timeout": 2,
            "socket_timeout": 3,
        },
        task_publish_retry=False,
        worker_hijack_root_logger=False,
        worker_enable_remote_control=False,
    )
    if url.startswith("rediss://"):
        app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
    return app


def get_celery_app():
    return _make_app(
        redis_url(), namespace(), number("STORY2SCRIPT_REDIS_VISIBILITY_TIMEOUT", 14400, minimum=60)
    )


app = get_celery_app()
