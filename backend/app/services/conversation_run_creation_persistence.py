"""Conversation run terminal-output and stale-sweep persistence (Task 10 I4).

Moved unchanged from ``conversation_run_service``, which re-exports every name.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import trace_storage
from app.services.artifact_service import finalize_artifacts_for_run
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_creation import RunStatus, transition_run, utc_now_naive

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
    ]
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
