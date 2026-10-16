from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    queue_input_not_found,
    require_queue_executable,
)
from app.services.conversation_run_queue_payload import JsonValue, validate_queue_input_payload
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

QueueInputSource = Literal["chat"]


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
    context: OrgContext,
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
            context=context,
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
    context: OrgContext,
    client_request_id: str,
    source: QueueInputSource,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
    checkpoint_id: str | None,
    priority: int,
) -> EnqueuedConversationInput:
    """Persist an idempotent input without allocating an active run."""
    user_id = context.user_id
    input_payload = validate_queue_input_payload(input_payload)
    agent_id = await require_queue_executable(db, conversation_id, context)
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    await ensure_conversation_runtime_policy(db, ConversationPolicyTarget(conversation.id, context))
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
        agent_id=agent_id,
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
    context: OrgContext,
    client_request_id: str,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
) -> ConversationRunInput:
    """Bind an accepted direct chat input to its already-created run."""
    conversation = await lock_owned_conversation(
        db, conversation_id=run.conversation_id, context=context
    )
    if run.user_id != context.user_id or run.agent_id != conversation.agent_id:
        raise queue_input_not_found()
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
