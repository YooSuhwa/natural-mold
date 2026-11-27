"""Conversation run terminal-output and stale-sweep persistence (Task 10 I4).

Moved unchanged from ``conversation_run_service``, which re-exports every name.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agent_runtime import event_names
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import (
    RUN_ACTIVE_STATUSES,
    ConversationRun,
    ConversationRunToolReceipt,
)
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.services import trace_storage
from app.services.artifact_service import finalize_artifacts_for_run
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_creation import RunStatus, utc_now_naive
from app.services.conversation_run_state import transition_run

logger = logging.getLogger("app.services.conversation_run_service")


async def finalize_run_outputs_for_status(
    db: AsyncSession,
    run: ConversationRun,
    status: RunStatus,
    *,
    append_terminal_event: bool = False,
    terminal_event: dict[str, Any] | None = None,
) -> None:
    if terminal_event is None and append_terminal_event and status == "canceled":
        event_id = f"{run.id}-canceled"
        terminal_event = {
            "id": event_id,
            "event": event_names.MESSAGE_END,
            "data": {"usage": {}, "content": "", "status": "canceled"},
        }
    if terminal_event is not None:
        event_id = terminal_event.get("id")
        await trace_storage.append_events(
            db,
            conversation_id=run.conversation_id,
            assistant_msg_id=str(run.id),
            events_chunk=[terminal_event],
            status="completed" if status in {"completed", "interrupted", "canceled"} else "failed",
        )
        if isinstance(event_id, str) and event_id:
            run.last_event_id = event_id
    if status not in {"canceled", "stale", "failed"}:
        return
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=str(run.id),
        status="completed" if status == "canceled" else "failed",
        conversation_id=run.conversation_id,
    )
    conversation = await db.get(Conversation, run.conversation_id)
    if conversation is None:
        return
    await finalize_artifacts_for_run(
        db,
        context=OrgContext(
            user_id=conversation.user_id,
            org_id=conversation.org_id,
            tenant_id=conversation.tenant_id,
        ),
        conversation_id=run.conversation_id,
        assistant_msg_id=str(run.id),
        run_status=status,
    )


def _unstarted_independent_handoff() -> ColumnElement[bool]:
    """The durable queue owns this initial claim until a worker starts it."""
    run, job, item = ConversationRun, IndependentChatJob, ConversationRunInput
    parent = aliased(ConversationRun)
    accepted = (
        select(job.id)
        .select_from(job)
        .join(parent, parent.id == job.parent_run_id)
        .join(Conversation, Conversation.id == job.child_conversation_id)
        .join(item, item.run_id == job.child_run_id)
        .where(
            job.child_run_id == run.id,
            job.child_conversation_id == run.conversation_id,
            job.child_agent_id == run.agent_id,
            job.user_id == run.user_id,
            job.org_id == run.org_id,
            job.tenant_id == run.tenant_id,
            job.cancel_requested_at.is_(None),
            parent.conversation_id == job.parent_conversation_id,
            parent.agent_id == job.parent_agent_id,
            parent.user_id == job.user_id,
            parent.org_id == job.org_id,
            parent.tenant_id == job.tenant_id,
            Conversation.source == INDEPENDENT_JOB_SOURCE,
            Conversation.agent_id == job.child_agent_id,
            Conversation.user_id == job.user_id,
            Conversation.org_id == job.org_id,
            Conversation.tenant_id == job.tenant_id,
            item.conversation_id == job.child_conversation_id,
            item.agent_id == job.child_agent_id,
            item.user_id == job.user_id,
            item.status == "claimed",
            item.source == "chat",
            item.claimed_at.is_not(None),
            item.checkpoint_id.is_(None),
            # The launch input's key is str(job.id). SQLite stores UUID columns as hex.
            item.client_request_id.like("________-____-____-____-____________"),
            func.replace(item.client_request_id, "-", "")
            == func.replace(cast(job.id, String), "-", ""),
        )
        .correlate(run)
        .exists()
    )
    tool_receipt = (
        select(ConversationRunToolReceipt.run_id)
        .where(ConversationRunToolReceipt.run_id == run.id)
        .correlate(run)
        .exists()
    )
    return and_(
        run.status == "queued",
        run.source == "start",
        run.run_source == "CHAT",
        run.parent_run_id.is_(None),
        run.worker_instance_id.is_(None),
        run.owner_generation == 0,
        run.owner_token.is_(None),
        run.started_at.is_(None),
        run.heartbeat_at.is_(None),
        run.completed_at.is_(None),
        run.cancel_requested_at.is_(None),
        run.runtime_materialized_at.is_(None),
        run.runtime_materialization_sha256.is_(None),
        run.recovery_model_attempts.is_(None),
        run.last_event_id.is_(None),
        ~tool_receipt,
        accepted,
    )


async def mark_stale_active_runs(
    db: AsyncSession,
    *,
    stale_before: datetime,
    worker_instance_id: str | None,
    include_workerless: bool,
    protected_run_ids: Sequence[uuid.UUID] | None = None,
    conversation_id: uuid.UUID | None = None,
) -> int:
    reference_time = func.coalesce(
        ConversationRun.heartbeat_at,
        ConversationRun.started_at,
        ConversationRun.created_at,
    )
    conditions = [
        ConversationRun.is_active.is_(True),
        ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
        reference_time <= stale_before,
        ConversationRun.status != "canceling",
        _unstarted_independent_handoff().is_not(True),
    ]
    # Enrolled owners and their cancellation state belong to the fenced recovery sweep,
    # including the first sweep after an administrator disables new takeovers.
    conditions.append(ConversationRun.owner_generation == 0)
    if conversation_id is not None:
        conditions.append(ConversationRun.conversation_id == conversation_id)
    if protected_run_ids:
        conditions.append(ConversationRun.id.notin_(list(protected_run_ids)))
    if worker_instance_id is not None:
        ownership_conditions = [ConversationRun.worker_instance_id == worker_instance_id]
        if include_workerless:
            ownership_conditions.append(ConversationRun.worker_instance_id.is_(None))
        conditions.append(or_(*ownership_conditions))
    elif not include_workerless:
        conditions.append(ConversationRun.worker_instance_id.is_not(None))

    result = await db.execute(select(ConversationRun).where(*conditions).with_for_update())
    runs = list(result.scalars().all())
    marked = 0
    for run in runs:
        try:
            await transition_run(
                db,
                run,
                "stale",
                error_code="stale_heartbeat",
                error_message="Run heartbeat exceeded the stale threshold.",
            )
        except ValueError:
            # 다른 경로(worker finalize/cancel)가 먼저 terminal 전이를 끝낸 run.
            # 한 run 의 경합이 같은 배치의 나머지 sweep 을 막지 않도록 skip.
            logger.warning(
                "skipping stale sweep for run %s: already transitioned to %s",
                run.id,
                run.status,
            )
            continue
        await finalize_run_outputs_for_status(db, run, "stale")
        await record_conversation_run_audit(
            db,
            action="conversation.run_stale",
            run=run,
            status="stale",
        )
        marked += 1
    return marked


async def _pause_uncertain_owned_run_queues(
    db: AsyncSession,
    *,
    stale_before: datetime,
    protected_run_ids: Sequence[uuid.UUID],
) -> None:
    reference_time = func.coalesce(
        ConversationRun.heartbeat_at,
        ConversationRun.started_at,
        ConversationRun.created_at,
    )
    pending_input_exists = (
        select(ConversationRunInput.id)
        .where(
            ConversationRunInput.conversation_id == ConversationRun.conversation_id,
            ConversationRunInput.status == "pending",
        )
        .exists()
    )
    conditions = [
        ConversationRun.is_active.is_(True),
        ConversationRun.worker_instance_id.is_not(None),
        reference_time <= stale_before,
        pending_input_exists,
    ]
    conditions.append(ConversationRun.owner_generation == 0)
    if protected_run_ids:
        conditions.append(ConversationRun.id.notin_(list(protected_run_ids)))
    conversation_ids = list(
        (
            await db.scalars(select(ConversationRun.conversation_id).where(*conditions).distinct())
        ).all()
    )
    if not conversation_ids:
        return
    conversations = list(
        (
            await db.scalars(
                select(Conversation)
                .where(Conversation.id.in_(conversation_ids))
                .order_by(Conversation.id)
                .with_for_update()
            )
        ).all()
    )
    now = utc_now_naive()
    for conversation in conversations:
        conversation.queue_paused = True
        conversation.queue_paused_at = conversation.queue_paused_at or now
    await db.flush()


async def sweep_stale_conversation_runs(
    *,
    session_factory: Callable[[], AsyncSession],
) -> None:
    """Mark active conversation runs stale after their heartbeat threshold.

    BE-S9 — 본문은 ``app.scheduler``에서 이관. keep-cron-alive try/except 포함.
    """
    # conversation_run_worker imports this module — keep the import call-local.
    from app.services.conversation_run_worker import get_run_task_registry

    stale_before = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=settings.chat_run_stale_after_seconds
    )
    try:
        # Task 10 I19: orphaned paired runs fail ``prepared_run_lease_missing``.
        from app.services.conversation_run_prepared_recovery import (
            terminalize_orphaned_prepared_runs,
        )

        await terminalize_orphaned_prepared_runs(
            session_factory=session_factory, stale_before=stale_before
        )
        from app.services.conversation_run_recovery import recover_stale_chat_runs

        await recover_stale_chat_runs(session_factory=session_factory, stale_before=stale_before)
        async with session_factory() as db:
            registry = get_run_task_registry()
            await _pause_uncertain_owned_run_queues(
                db,
                stale_before=stale_before,
                protected_run_ids=tuple(registry.active_run_ids()),
            )
            marked = await mark_stale_active_runs(
                db,
                stale_before=stale_before,
                worker_instance_id=None,
                include_workerless=True,
                protected_run_ids=tuple(registry.active_run_ids()),
            )
            await db.commit()
        if marked:
            logger.warning("Marked %d stale conversation run(s)", marked)
    except Exception:  # noqa: BLE001 — keep cron alive
        logger.exception("Conversation run stale sweep failed; will retry next run")
