"""Worker-restart recovery for prepared runs (parent Task 10, I19).

A run carrying the durable materialization pair (``runtime_materialized_at`` +
digest) was committed ``running`` by a process that held its lease. The lease
is process-local and never reconstructible, so once no owner can still hold it
the run terminalizes without reading any Agent/User/config or rebuilding:
``running`` fails with ``prepared_run_lease_missing`` (linked claimed inputs
fail too); ``canceling`` becomes ``canceled`` with the dead owner's
cancellation acknowledged (so the queue is not left paused).

Candidates are paired, active runs not owned by a live local task (that task
terminalizes its own run). For a row owned by *this* worker instance the
prepared registry is authoritative (``ensure_live_for_marker`` under the
canonical gateway locks). A row owned by another worker is treated as
orphaned only when its heartbeat is stale — that is the only cross-process
liveness signal; a remote owner with a fresh heartbeat is left alone. Runs
without the pair stay with the ordinary queue recovery, which may freshly
prepare them. Each row is isolated: one failure never stops the sweep. Entry
is gated by ``settings.prepared_run_lease_enabled``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.conversation_scope import resolve_conversation_actor
from app.config import settings
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_state import transition_run
from app.services.prepared_run_state import fail_prepared_run

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


async def _candidates(
    db: AsyncSession, *, worker_instance_id: str, stale_before: datetime
) -> list[Any]:
    reference_time = func.coalesce(
        ConversationRun.heartbeat_at, ConversationRun.started_at, ConversationRun.created_at
    )
    result = await db.execute(
        select(
            ConversationRun.id,
            ConversationRun.conversation_id,
            ConversationRun.agent_id,
            ConversationRun.user_id,
        ).where(
            ConversationRun.runtime_materialized_at.is_not(None),
            ConversationRun.runtime_materialization_sha256.is_not(None),
            ConversationRun.is_active.is_(True),
            ConversationRun.status.in_(("running", "canceling")),
            or_(
                ConversationRun.worker_instance_id == worker_instance_id,
                reference_time <= stale_before,
            ),
        )
    )
    return list(result.all())


async def _terminalize(session_factory: SessionFactory, row: Any) -> bool:
    async with session_factory() as db:
        try:
            context = await resolve_conversation_actor(db, row.conversation_id, row.user_id)
        except AppError:
            return False
        target = RunExecutionTarget(
            row.id, RunConversationTarget(row.conversation_id, row.agent_id, context)
        )
        run = await lock_executable_run(db, target)
        digest = None if run is None else run.runtime_materialization_sha256
        if run is None or not run.is_active or digest is None:
            await db.rollback()
            return False
        try:
            get_prepared_run_registry().ensure_live_for_marker(run.id, marker_digest=digest)
        except PreparedRunLeaseError as error:
            if error.code != LEASE_MISSING:
                await db.rollback()
                return False
            if run.status == "canceling":
                await transition_run(
                    db, run, "canceled", cancellation_ack_worker_id=run.worker_instance_id
                )
            else:
                await fail_prepared_run(db, run, LEASE_MISSING)
            await db.commit()
            return True
        await db.rollback()
        return False


async def terminalize_orphaned_prepared_runs(
    *, session_factory: SessionFactory, stale_before: datetime
) -> list[uuid.UUID]:
    """Terminalize every orphaned paired run; return the terminalized run IDs."""

    if not settings.prepared_run_lease_enabled:
        return []
    # conversation_run_worker imports the sweep module — keep the import call-local.
    from app.services.conversation_run_worker import get_run_task_registry

    tasks = get_run_task_registry()
    async with session_factory() as db:
        rows = await _candidates(
            db, worker_instance_id=tasks.worker_instance_id, stale_before=stale_before
        )
    failed: list[uuid.UUID] = []
    for row in rows:
        if row.agent_id is None or row.id in tasks.active_run_ids():
            continue
        try:
            if await _terminalize(session_factory, row):
                failed.append(row.id)
        except Exception:  # noqa: BLE001 - isolate rows; the next sweep retries
            logger.exception("prepared run recovery failed run_id=%s", row.id)
    return failed
