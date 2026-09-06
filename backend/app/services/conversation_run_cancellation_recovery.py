from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_audit_service import record_conversation_run_audit


async def finalize_workerless_cancel_before_start(
    *,
    session_factory: Callable[[], AsyncSession],
    protected_run_ids: Sequence[uuid.UUID],
) -> list[uuid.UUID]:
    """ACK cancellation only after locking and proving no worker owns the run."""
    async with session_factory() as db:
        conditions = [
            ConversationRun.status == "canceling",
            ConversationRun.is_active.is_(True),
            ConversationRun.worker_instance_id.is_(None),
        ]
        if protected_run_ids:
            conditions.append(ConversationRun.id.notin_(list(protected_run_ids)))
        candidates = list(
            (
                await db.execute(
                    select(
                        ConversationRun.id,
                        ConversationRun.conversation_id,
                        ConversationRun.user_id,
                    ).where(*conditions)
                )
            ).all()
        )

    finalized_conversations: list[uuid.UUID] = []
    for run_id, conversation_id, user_id in candidates:
        async with session_factory() as db:
            await conversation_run_queue_service.lock_owned_conversation(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            run = await db.get(ConversationRun, run_id, with_for_update=True)
            if (
                run is None
                or run.status != "canceling"
                or not run.is_active
                or run.worker_instance_id is not None
            ):
                continue
            bound_input = await db.scalar(
                select(ConversationRunInput)
                .where(ConversationRunInput.run_id == run.id)
                .with_for_update()
            )
            await conversation_run_service.transition_run(
                db,
                run,
                "canceled",
                allow_workerless_cancellation_ack=True,
            )
            if bound_input is not None:
                bound_input.status = "canceled"
                bound_input.run_id = None
                bound_input.revision += 1
            await conversation_run_service.finalize_run_outputs_for_status(
                db,
                run,
                "canceled",
                append_terminal_event=True,
            )
            await record_conversation_run_audit(
                db,
                action="conversation.run_canceled",
                run=run,
                status="canceled",
            )
            await db.commit()
            finalized_conversations.append(conversation_id)
    return finalized_conversations


__all__ = ["finalize_workerless_cancel_before_start"]
