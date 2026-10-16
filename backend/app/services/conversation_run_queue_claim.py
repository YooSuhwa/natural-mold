from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import lock_query
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    queue_input_not_found,
    require_queue_executable,
)


@dataclass(frozen=True, slots=True)
class ClaimedConversationInput:
    input: ConversationRunInput
    run: ConversationRun


async def claim_next_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ClaimedConversationInput | None:
    """Bind the next eligible input to a run under the conversation row lock."""
    user_id = context.user_id
    agent_id = await require_queue_executable(db, conversation_id, context)
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if conversation.queue_paused:
        return None
    active = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if active is not None:
        return None
    unacknowledged_predecessor = await db.scalar(
        select(ConversationRun.id).where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.user_id == user_id,
            ConversationRun.cancel_requested_at.is_not(None),
            ConversationRun.cancellation_acknowledged_at.is_(None),
        )
    )
    if unacknowledged_predecessor is not None:
        conversation.queue_paused = True
        conversation.queue_paused_at = conversation.queue_paused_at or utc_now_naive()
        await db.flush()
        return None

    queued = await lock_query(
        db,
        LockClass.INPUT,
        select(ConversationRunInput)
        .where(
            ConversationRunInput.conversation_id == conversation_id,
            ConversationRunInput.user_id == user_id,
            ConversationRunInput.status == "pending",
        )
        .order_by(
            ConversationRunInput.priority.desc(),
            ConversationRunInput.position,
            ConversationRunInput.created_at,
        )
        .limit(1)
        .with_for_update(),
    )
    if queued is None:
        return None
    if queued.agent_id is None or queued.agent_id != agent_id:
        raise queue_input_not_found()
    interrupted = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if interrupted is not None:
        return None

    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            input_preview=None,
            run_metadata={
                "queue_input_id": str(queued.id),
                "client_request_id": queued.client_request_id,
                "checkpoint_id": queued.checkpoint_id,
            },
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    if run is None:
        raise RuntimeError("accepted conversation run was not persisted")
    queued.run_id = run.id
    queued.status = "claimed"
    queued.claimed_at = utc_now_naive()
    queued.revision += 1
    await db.flush()
    return ClaimedConversationInput(input=queued, run=run)
