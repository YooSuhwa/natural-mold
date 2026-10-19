from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_input_scope import owned_input_predicate
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import lock_query
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    queue_input_not_found,
    require_queue_executable,
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
    context: OrgContext,
    expected_revision: int,
) -> PromotedConversationInput:
    """Promote one pending input and persist predecessor cancellation in one transaction."""
    await require_queue_executable(db, conversation_id, context)
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if conversation.queue_paused:
        raise _conflict("Resume the conversation queue before steering")

    # Canonical order locks the active Run before any Input row (the held
    # Conversation lock already serializes both); validation order is unchanged.
    active = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if active is not None:
        active = await conversation_run_service.get_run_for_user(
            db,
            conversation_id=conversation_id,
            run_id=active.id,
            context=context,
            for_update=True,
        )
    queued = await lock_query(
        db,
        LockClass.INPUT,
        select(ConversationRunInput)
        .where(
            ConversationRunInput.id == input_id,
            ConversationRunInput.conversation_id == conversation_id,
            owned_input_predicate(context),
        )
        .with_for_update(),
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
        context=context,
    )
    if interrupted is not None:
        raise _conflict("Resolve the pending approval before steering")

    queued.priority = _INTERRUPT_PRIORITY
    queued.revision += 1
    if active is not None:
        await conversation_run_service.request_cancel_run(db, active, reason="steer")
    await db.flush()
    return PromotedConversationInput(
        input=queued,
        predecessor_run_id=active.id if active is not None else None,
    )
