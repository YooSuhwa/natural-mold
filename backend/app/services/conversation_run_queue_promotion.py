from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    queue_input_not_found,
)

_INTERRUPT_PRIORITY = 100


@dataclass(frozen=True, slots=True)
class PromotedConversationInput:
    input: ConversationRunInput
    predecessor_run_id: uuid.UUID | None


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


async def promote_pending_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_revision: int,
) -> PromotedConversationInput:
    """Promote one pending input and persist predecessor cancellation in one transaction."""
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if conversation.queue_paused:
        raise _conflict("Resume the conversation queue before steering")

    queued = await db.scalar(
        select(ConversationRunInput)
        .where(
            ConversationRunInput.id == input_id,
            ConversationRunInput.conversation_id == conversation_id,
            ConversationRunInput.user_id == user_id,
        )
        .with_for_update()
    )
    if queued is None:
        raise queue_input_not_found()
    if queued.status != "pending":
        raise _conflict("Only pending conversation inputs can be promoted")
    if queued.revision != expected_revision:
        raise _conflict("Conversation input revision is stale")
    if queued.checkpoint_id is not None:
        raise _conflict("Queued edit and regenerate requests cannot be promoted")
    if queued.priority >= _INTERRUPT_PRIORITY:
        raise _conflict("Conversation input is already promoted")

    interrupted = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if interrupted is not None:
        raise _conflict("Resolve the pending approval before steering")

    active = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if active is not None:
        active = await conversation_run_service.get_run_for_user(
            db,
            conversation_id=conversation_id,
            run_id=active.id,
            user_id=user_id,
            for_update=True,
        )

    queued.priority = _INTERRUPT_PRIORITY
    queued.revision += 1
    if active is not None:
        await conversation_run_service.request_cancel_run(db, active, reason="steer")
    await db.flush()
    return PromotedConversationInput(
        input=queued,
        predecessor_run_id=active.id if active is not None else None,
    )
