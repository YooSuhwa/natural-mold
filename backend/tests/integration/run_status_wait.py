"""Wait for a conversation run to reach a terminal status on the shared unit engine."""

from __future__ import annotations

import asyncio
import uuid

from app.models.conversation_run import ConversationRun
from app.services.conversation_run_worker import get_run_task_registry
from tests.conftest import TestSession


async def wait_for_run_status(
    run_id: str,
    status: str,
    *,
    # 2s flaked on CI (2-core runner + xdist -n 4 starves the background
    # worker task — PR #280 first run). The wait returns as soon as the
    # status lands, so a generous deadline costs nothing when healthy.
    timeout_seconds: float = 15.0,
) -> ConversationRun:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    # The unit engine is ONE shared SQLite connection (StaticPool). Polling while the
    # worker writes lets the poll's session close ROLLBACK the worker's in-flight
    # terminal transition on that connection; the worker's commit then lands nothing
    # and the row stays ``running`` (PR #314 CI, reproduced under CPU load). Callers
    # wait for a terminal status, which the worker task writes before it ends, so wait
    # for the local task first and read afterwards.
    task = get_run_task_registry().get(uuid.UUID(run_id))
    if task is not None:
        await asyncio.wait({task}, timeout=timeout_seconds)
    last_seen: str | None = None
    while loop.time() < deadline:
        async with TestSession() as db:
            run = await db.get(ConversationRun, uuid.UUID(run_id))
            if run is not None:
                last_seen = run.status
                if run.status == status:
                    return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"timeout waiting for run {run_id} status={status}; last={last_seen}")
