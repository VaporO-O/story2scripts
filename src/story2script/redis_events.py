"""Shared Redis Stream -> browser SSE, with database terminal-state fallback."""

import json

import anyio
from fastapi.responses import StreamingResponse

from .redis_runtime import RedisUnavailable, read_events


def shared_job_stream(store, job_id, request):
    async def frames():
        cursor, attempt = "0-0", None
        idle = 0
        while not await request.is_disconnected():
            row = await anyio.to_thread.run_sync(store._row, job_id)
            current = f"{job_id}:{row['generation']}:{row['attempts']}"
            if current != attempt:
                cursor, attempt = "0-0", current
            try:
                events = await anyio.to_thread.run_sync(read_events, current, cursor)
            except RedisUnavailable:
                # Ending without done makes the existing browser client use polling.
                return
            for event_id, event in events:
                cursor = event_id
                yield f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    return
            if row["status"] in ("succeeded", "failed") and len(events) < 100:
                # Events can expire or be lost; result/status are always in SQLite.
                done = {
                    "type": "done",
                    **{k: row[k] for k in ("status", "progress", "stage", "message")},
                }
                yield "data: " + json.dumps(done, ensure_ascii=False) + "\n\n"
                return
            idle = 0 if events else idle + 0.5
            if idle >= 15:
                yield ": keepalive\n\n"
                idle = 0
            await anyio.sleep(0.5)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
