"""Keep an AG-UI queue item a single, server-identified user turn across edits."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.schemas.chat_resource_context import (
    INTERNAL_RESOURCE_CONTEXT_KEY,
    PUBLIC_RESOURCE_CONTEXT_KEY,
)
from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY, is_omitted_user_message
from app.services.ag_ui_queue_branch import bound_ag_ui_input
from app.services.ag_ui_start_effects import authorize_start_attachments
from app.services.ag_ui_start_input import OMITTED_MEDIA_SUFFIX, validate_chat_text
from app.services.chat_resource_context_payload import public_resource_context_from_payload


class _TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["text"]
    text: str


class _EditMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user"]
    id: str | None = None
    content: str | list[_TextPart]
    # assistant-ui's native queue editor includes an empty custom-metadata object.
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=0)


def _invalid_stored_input() -> HTTPException:
    return HTTPException(409, "The stored AG-UI input no longer matches its command")


def _stored_omission(queued: ConversationRunInput) -> bool:
    """Validate even rows updated by a worker running the old graph-input editor."""
    payload = queued.input_payload
    if (
        queued.source != "chat"
        or not isinstance(payload, dict)
        or payload.keys() - {"messages", INTERNAL_RESOURCE_CONTEXT_KEY}
    ):
        raise _invalid_stored_input()
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        raise _invalid_stored_input()
    message = messages[0]
    keys = {"role", "id", "content"}
    if (
        message.get("role") != "user"
        or message.get("id") != str(queued.id)
        or not isinstance(text := message.get("content"), str)
    ):
        raise _invalid_stored_input()
    validate_chat_text(text)
    omitted = is_omitted_user_message(message)
    if omitted:
        if not queued.client_request_id.endswith(OMITTED_MEDIA_SUFFIX):
            raise _invalid_stored_input()
        keys.add("additional_kwargs")
    if set(message) != keys or (
        not text.strip()
        and not omitted
        and not queued.attachment_ids
        and not public_resource_context_from_payload(payload)
    ):
        raise _invalid_stored_input()
    return omitted


async def _authorized_omission(
    db: AsyncSession, context: OrgContext, queued: ConversationRunInput
) -> bool:
    omitted = _stored_omission(queued)
    try:
        if not isinstance(queued.attachment_ids, list):
            raise ValueError
        uploads = [UUID(value) for value in queued.attachment_ids]
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid_stored_input() from exc
    await authorize_start_attachments(
        db, context, queued.conversation_id, uploads, require_linked=True
    )
    return omitted


async def validate_ag_ui_queue_delivery(
    db: AsyncSession, context: OrgContext, queued: ConversationRunInput
) -> bool:
    """Return the true omission marker only after verifying its bound input and files."""
    if not await db.scalar(select(bound_ag_ui_input(context, queued.conversation_id, queued.id))):
        return False
    return await _authorized_omission(db, context, queued)


async def public_queue_edit(
    db: AsyncSession,
    context: OrgContext,
    queued: ConversationRunInput,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Preserve native graph edits for legacy rows; validate a bound command's user delta."""
    if not await db.scalar(select(bound_ag_ui_input(context, queued.conversation_id, queued.id))):
        return payload
    omitted = await _authorized_omission(db, context, queued)
    if payload.keys() - {"messages", PUBLIC_RESOURCE_CONTEXT_KEY}:
        raise HTTPException(422, "Unsupported AG-UI queue input fields")
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        raise HTTPException(422, "A queue edit requires one user message")
    try:
        edit = _EditMessage.model_validate(messages[0])
    except ValidationError as exc:
        raise HTTPException(422, "Invalid AG-UI queue message") from exc
    if edit.id is not None and edit.id != str(queued.id):
        raise HTTPException(422, "A queue edit cannot replace the bound user message")
    text = (
        edit.content
        if isinstance(edit.content, str)
        else "".join(part.text for part in edit.content)
    )
    validate_chat_text(text)
    message: dict[str, JsonValue] = {"role": "user", "id": str(queued.id), "content": text}
    if omitted and not text.strip():
        message["content"] = ""
        message["additional_kwargs"] = {OMITTED_MEDIA_KEY: True}
    normalized: dict[str, JsonValue] = {"messages": [message]}
    if PUBLIC_RESOURCE_CONTEXT_KEY in payload:
        normalized[PUBLIC_RESOURCE_CONTEXT_KEY] = payload[PUBLIC_RESOURCE_CONTEXT_KEY]
    else:
        refs = public_resource_context_from_payload(queued.input_payload)
        if refs:
            normalized[PUBLIC_RESOURCE_CONTEXT_KEY] = [
                cast("dict[str, JsonValue]", dict(ref)) for ref in refs
            ]
    if (
        not text.strip()
        and not omitted
        and not queued.attachment_ids
        and not normalized.get(PUBLIC_RESOURCE_CONTEXT_KEY)
    ):
        raise HTTPException(
            422, "An edited user message requires text, an attachment, or a resource"
        )
    return normalized
