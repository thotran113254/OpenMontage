"""Server-sent events over the job's append-only progress log.

Tailing `events.jsonl` rather than piping the runner's stdout means a browser
reload replays the whole history (via Last-Event-ID) instead of showing an
empty log for a job that is already half done.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from lib.talking_head_edit.job_store import Job

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}


def _format(event: dict, event_id: int) -> str:
    return f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def event_stream(job: Job, start_offset: int = 0) -> AsyncIterator[str]:
    offset = start_offset
    idle_seconds = 0.0

    while True:
        events = list(job.read_events(offset))
        for event in events:
            offset += 1
            yield _format(event, offset)

        if events:
            idle_seconds = 0.0
        else:
            await asyncio.sleep(POLL_SECONDS)
            idle_seconds += POLL_SECONDS
            if idle_seconds >= HEARTBEAT_SECONDS:
                idle_seconds = 0.0
                yield ": heartbeat\n\n"     # keeps proxies from closing the stream

            try:
                status = job.load().get("status")
            except (OSError, ValueError):
                status = None
            if status in TERMINAL_STATUSES:
                # drain anything written between the last read and the status flip
                for event in job.read_events(offset):
                    offset += 1
                    yield _format(event, offset)
                # the client closes on this marker; it carries a timestamp so a
                # log view can render it like any other line
                yield _format(
                    {"ts": time.time(), "type": "stream_end", "stage": "",
                     "message": f"Kết thúc theo dõi (trạng thái: {status})", "status": status},
                    offset + 1,
                )
                return
