from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services.conversation_run_queue_payload import JsonValue, validate_queue_input_payload


def queue_input_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Conversation input not found")


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


async def lock_owned_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.id == conversation_id, Agent.user_id == user_id)
        .with_for_update(of=Conversation)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise queue_input_not_found()
    return conversation


async def list_inputs(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[ConversationRunInput]:
    await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    result = await db.execute(
        select(ConversationRunInput)
        .where(ConversationRunInput.conversation_id == conversation_id)
        .order_by(
            ConversationRunInput.priority.desc(),
            ConversationRunInput.position,
            ConversationRunInput.created_at,
        )
    )
    return list(result.scalars().all())


async def _pending_input_for_update(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_revision: int,
) -> ConversationRunInput:
    await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
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
        raise _conflict("Only pending conversation inputs can be changed")
    if queued.revision != expected_revision:
        raise _conflict("Conversation input revision is stale")
    return queued


async def edit_pending_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_revision: int,
    input_payload: dict[str, JsonValue],
) -> ConversationRunInput:
    input_payload = validate_queue_input_payload(input_payload)
    if "attachments" in input_payload:
        raise _conflict("Queued attachments cannot be changed through input edit")
    queued = await _pending_input_for_update(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        user_id=user_id,
        expected_revision=expected_revision,
    )
    queued.input_payload = input_payload
    queued.revision += 1
    await db.flush()
    return queued


async def delete_pending_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_revision: int,
) -> ConversationRunInput:
    queued = await _pending_input_for_update(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        user_id=user_id,
        expected_revision=expected_revision,
    )
    queued.status = "canceled"
    queued.revision += 1
    await db.flush()
    return queued


async def reorder_pending_inputs(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    ordered_input_ids: list[uuid.UUID],
    expected_revisions: dict[uuid.UUID, int],
) -> list[ConversationRunInput]:
    await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    result = await db.execute(
        select(ConversationRunInput)
        .where(
            ConversationRunInput.conversation_id == conversation_id,
            ConversationRunInput.user_id == user_id,
            ConversationRunInput.status == "pending",
        )
        .with_for_update()
    )
    pending = list(result.scalars().all())
    by_id = {item.id: item for item in pending}
    if len(ordered_input_ids) != len(set(ordered_input_ids)) or set(ordered_input_ids) != set(
        by_id
    ):
        raise _conflict("Reorder must include every pending conversation input exactly once")
    for position, input_id in enumerate(ordered_input_ids, start=1):
        item = by_id[input_id]
        if expected_revisions.get(input_id) != item.revision:
            raise _conflict("Conversation input revision is stale")
        item.position = position
        item.revision += 1
    await db.flush()
    return [by_id[input_id] for input_id in ordered_input_ids]


async def pause_queue(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    conversation.queue_paused = True
    conversation.queue_paused_at = conversation.queue_paused_at or utc_now_naive()
    await db.flush()
    return conversation


async def resume_queue(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    conversation.queue_paused = False
    conversation.queue_paused_at = None
    await db.flush()
    return conversation


__all__ = [
    "delete_pending_input",
    "edit_pending_input",
    "list_inputs",
    "lock_owned_conversation",
    "pause_queue",
    "queue_input_not_found",
    "reorder_pending_inputs",
    "resume_queue",
]
