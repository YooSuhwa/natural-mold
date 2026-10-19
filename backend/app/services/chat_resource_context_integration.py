from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_input_scope import conversation_input_parent
from app.authz.conversation_run_scope import RunConversationTarget, lock_run_conversation
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_parent_scoped
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.schemas.chat_resource_context import (
    ChatResourceContextRequest,
    FrozenChatResourceContext,
)
from app.services.chat_resource_context import ChatResourceContextResolver
from app.services.chat_resource_context_errors import (
    ResourceContextLimitError,
    ResourceContextNotFoundError,
)
from app.services.chat_resource_context_payload import (
    PUBLIC_RESOURCE_CONTEXT_KEY,
    apply_frozen_resource_context,
    extract_resource_context_request,
    frozen_resource_context_from_payload,
    public_input_payload,
    public_resource_context_from_payload,
    public_resource_reference,
    resource_context_user_message,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_run_queue_mutations import queue_input_not_found


@dataclass(frozen=True, slots=True)
class QueuedResourceContextInput:
    conversation_id: uuid.UUID
    client_request_id: str
    input_payload: dict[str, JsonValue]


async def freeze_resource_context_payload(
    db: AsyncSession,
    scope: ResourceContextScope,
    input_payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    sanitized, request = extract_resource_context_request(input_payload)
    if request is None:
        return sanitized
    frozen = await _resolve_or_http_error(db, scope, request)
    return apply_frozen_resource_context(sanitized, frozen)


async def freeze_queued_resource_context_payload(
    db: AsyncSession,
    scope: ResourceContextScope,
    queued_request: QueuedResourceContextInput,
) -> dict[str, JsonValue]:
    """Authorize an execution input, preserving an accepted retry byte-for-byte."""

    if queued_request.conversation_id != scope.conversation_id:
        raise queue_input_not_found()
    try:
        await require_private_actor(db, scope.context)
    except AppError as exc:
        raise queue_input_not_found() from exc
    conversation = await lock_run_conversation(
        db, RunConversationTarget(scope.conversation_id, scope.agent_id, scope.context)
    )
    if conversation is None:
        raise queue_input_not_found()
    sanitized, request = extract_resource_context_request(queued_request.input_payload)
    existing = (
        await db.execute(
            select(
                ConversationRunInput.id,
                ConversationRunInput.user_id,
                ConversationRunInput.agent_id,
            )
            .where(
                ConversationRunInput.conversation_id == conversation.id,
                ConversationRunInput.client_request_id == queued_request.client_request_id,
            )
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if existing is not None:
        if existing.user_id != conversation.user_id or existing.agent_id != conversation.agent_id:
            raise queue_input_not_found()
        stored_payload = await db.scalar(
            private_parent_scoped(
                select(ConversationRunInput.input_payload).where(
                    ConversationRunInput.id == existing.id,
                    Conversation.id == conversation.id,
                    Conversation.agent_id == scope.agent_id,
                ),
                conversation_input_parent(),
                scope.context,
            )
        )
        if stored_payload is None:
            raise queue_input_not_found()
        existing_refs = public_resource_context_from_payload(stored_payload)
        incoming_refs = (
            [public_resource_reference(item) for item in request.resources]
            if request is not None
            else []
        )
        if public_input_payload(stored_payload) == sanitized and existing_refs == incoming_refs:
            return stored_payload
        return sanitized
    if request is None:
        return sanitized
    frozen = await _resolve_or_http_error(db, scope, request)
    return apply_frozen_resource_context(sanitized, frozen)


async def reauthorize_resource_context_payload(
    db: AsyncSession,
    scope: ResourceContextScope,
    input_payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if PUBLIC_RESOURCE_CONTEXT_KEY in input_payload:
        raise HTTPException(status_code=422, detail="Stored resource context is invalid")
    frozen = frozen_resource_context_from_payload(input_payload)
    if frozen is None:
        return input_payload
    try:
        await ChatResourceContextResolver(db, scope).reauthorize(frozen)
    except ResourceContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return resource_context_user_message(input_payload)


async def _resolve_or_http_error(
    db: AsyncSession,
    scope: ResourceContextScope,
    request: ChatResourceContextRequest,
) -> FrozenChatResourceContext:
    try:
        return await ChatResourceContextResolver(db, scope).resolve(request)
    except ResourceContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResourceContextLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = [
    "QueuedResourceContextInput",
    "freeze_queued_resource_context_payload",
    "freeze_resource_context_payload",
    "reauthorize_resource_context_payload",
]
