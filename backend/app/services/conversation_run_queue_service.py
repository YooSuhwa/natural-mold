from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.services.conversation_run_queue_mutations import (
    delete_pending_input,
    edit_pending_input,
    list_inputs,
    lock_owned_conversation,
    pause_queue,
    queue_input_not_found,
    reorder_pending_inputs,
    resume_queue,
)
from app.services.conversation_run_queue_payload import JsonValue, validate_queue_input_payload
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

QueueInputSource = Literal["chat"]


@dataclass(frozen=True, slots=True)
class ClaimedConversationInput:
    input: ConversationRunInput
    run: ConversationRun


@dataclass(frozen=True, slots=True)
class EnqueuedConversationInput:
    input: ConversationRunInput
    created: bool


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


async def enqueue_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    client_request_id: str,
    source: QueueInputSource,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
    checkpoint_id: str | None,
    priority: int,
) -> ConversationRunInput:
    return (
        await enqueue_input_with_result(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            client_request_id=client_request_id,
            source=source,
            input_payload=input_payload,
            attachment_ids=attachment_ids,
            checkpoint_id=checkpoint_id,
            priority=priority,
        )
    ).input


async def enqueue_input_with_result(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    client_request_id: str,
    source: QueueInputSource,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
    checkpoint_id: str | None,
    priority: int,
) -> EnqueuedConversationInput:
    """Persist an idempotent input without allocating an active run."""
    input_payload = validate_queue_input_payload(input_payload)
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    await ensure_conversation_runtime_policy(db, conversation.id)
    existing = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.conversation_id == conversation_id,
            ConversationRunInput.client_request_id == client_request_id,
        )
    )
    attachment_values = [str(item) for item in attachment_ids]
    if existing is not None:
        same_request = (
            existing.input_payload == input_payload
            and existing.attachment_ids == attachment_values
            and existing.checkpoint_id == checkpoint_id
            and existing.source == source
        )
        if not same_request:
            raise _conflict("Client request id was already used for different input")
        return EnqueuedConversationInput(input=existing, created=False)

    max_position = await db.scalar(
        select(func.max(ConversationRunInput.position)).where(
            ConversationRunInput.conversation_id == conversation_id
        )
    )
    queued = ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=user_id,
        client_request_id=client_request_id,
        source=source,
        status="pending",
        priority=priority,
        position=(max_position or 0) + 1,
        revision=1,
        input_payload=input_payload,
        attachment_ids=attachment_values,
        checkpoint_id=checkpoint_id,
    )
    db.add(queued)
    await db.flush()
    return EnqueuedConversationInput(input=queued, created=True)


async def persist_direct_input(
    db: AsyncSession,
    *,
    run: ConversationRun,
    client_request_id: str,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
) -> ConversationRunInput:
    """Bind an accepted direct chat input to its already-created run."""
    existing = await db.scalar(
        select(ConversationRunInput.id).where(
            ConversationRunInput.conversation_id == run.conversation_id,
            ConversationRunInput.client_request_id == client_request_id,
        )
    )
    if existing is not None:
        raise _conflict("Client request id was already used for a conversation input")
    max_position = await db.scalar(
        select(func.max(ConversationRunInput.position)).where(
            ConversationRunInput.conversation_id == run.conversation_id
        )
    )
    persisted = ConversationRunInput(
        conversation_id=run.conversation_id,
        agent_id=run.agent_id,
        user_id=run.user_id,
        run_id=run.id,
        client_request_id=client_request_id,
        source="chat",
        status="claimed",
        priority=0,
        position=(max_position or 0) + 1,
        revision=1,
        input_payload=input_payload,
        attachment_ids=[str(item) for item in attachment_ids],
        checkpoint_id=None,
        claimed_at=utc_now_naive(),
    )
    db.add(persisted)
    await db.flush()
    return persisted


async def claim_next_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ClaimedConversationInput | None:
    """Bind the next eligible input to a run under the conversation row lock."""
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if conversation.queue_paused:
        return None
    active = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
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

    queued = await db.scalar(
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
        .with_for_update()
    )
    if queued is None:
        return None
    interrupted = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if interrupted is not None:
        return None

    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=queued.agent_id,
        user_id=user_id,
        source="chat",
        input_preview=None,
        metadata={
            "queue_input_id": str(queued.id),
            "client_request_id": queued.client_request_id,
            "checkpoint_id": queued.checkpoint_id,
        },
    )
    queued.run_id = run.id
    queued.status = "claimed"
    queued.claimed_at = utc_now_naive()
    queued.revision += 1
    await db.flush()
    return ClaimedConversationInput(input=queued, run=run)


__all__ = [
    "ClaimedConversationInput",
    "EnqueuedConversationInput",
    "claim_next_input",
    "delete_pending_input",
    "edit_pending_input",
    "enqueue_input",
    "enqueue_input_with_result",
    "list_inputs",
    "lock_owned_conversation",
    "pause_queue",
    "persist_direct_input",
    "queue_input_not_found",
    "reorder_pending_inputs",
    "resume_queue",
]
