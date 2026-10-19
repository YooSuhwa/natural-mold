from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.message_feedback import MessageFeedback
from app.schemas.conversation import (
    MessageAttachmentBrief,
    MessageFeedbackBrief,
    MessageResponse,
)
from app.schemas.conversation_history import JsonValue
from app.services.artifact_service import list_conversation_artifacts_by_message_id
from app.services.conversation_history_service import read_purged_history_content


@dataclass(frozen=True, slots=True)
class RetainedMessageReadRequest:
    db: AsyncSession
    conversation: Conversation
    user_id: UUID | None
    context: OrgContext


async def load_retained_messages(request: RetainedMessageReadRequest) -> list[MessageResponse]:
    """Project a complete sanitized manifest into the existing message DTO."""
    db = request.db
    conversation = request.conversation
    content = await read_purged_history_content(db, conversation)
    checkpoint_rows = [row for row in content.checkpoints if isinstance(row, dict)]
    raw_messages = [row for row in content.messages if isinstance(row, dict)]
    feedback = (
        {
            row.message_id: row.rating
            for row in await db.scalars(
                select(MessageFeedback).where(
                    MessageFeedback.user_id == request.user_id,
                    MessageFeedback.conversation_id == conversation.id,
                )
            )
        }
        if request.user_id is not None
        else {}
    )
    attachments: dict[str, list[MessageAttachmentBrief]] = {}
    if request.user_id is not None:
        rows = await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.conversation_id == conversation.id,
                MessageAttachment.message_id.is_not(None),
                private_scope_predicate(MessageAttachment, request.context),
            )
        )
        for row in rows:
            if row.message_id is not None:
                attachments.setdefault(row.message_id, []).append(
                    MessageAttachmentBrief.model_validate(row)
                )
    artifacts = (
        await list_conversation_artifacts_by_message_id(
            db,
            context=request.context,
            conversation_id=conversation.id,
        )
        if request.user_id is not None
        else {}
    )
    responses: list[MessageResponse] = []
    previous_id: UUID | None = None
    for index, raw in enumerate(raw_messages):
        raw_id = raw.get("id")
        runtime_id = raw_id if isinstance(raw_id, str) else None
        message_id = parse_msg_id(runtime_id, conversation.id, index)
        timestamp = conversation.message_timestamps.get(str(message_id))
        created_at = (
            datetime.fromisoformat(timestamp)
            if isinstance(timestamp, str)
            else conversation.created_at + timedelta(milliseconds=index)
        )
        sibling_pairs: list[tuple[UUID, str]] = []
        for checkpoint in checkpoint_rows:
            checkpoint_messages = checkpoint.get("messages")
            checkpoint_id = checkpoint.get("checkpoint_id")
            if not isinstance(checkpoint_messages, list) or not isinstance(checkpoint_id, str):
                continue
            if index >= len(checkpoint_messages):
                continue
            sibling_message = checkpoint_messages[index]
            if not isinstance(sibling_message, dict):
                continue
            sibling_raw_id = sibling_message.get("id")
            sibling_runtime_id = sibling_raw_id if isinstance(sibling_raw_id, str) else None
            pair = (parse_msg_id(sibling_runtime_id, conversation.id, index), checkpoint_id)
            if pair not in sibling_pairs:
                sibling_pairs.append(pair)
        linked_keys = tuple(key for key in (runtime_id, str(message_id)) if key is not None)
        raw_tool_calls = raw.get("tool_calls")
        tool_calls = (
            [dict(item) for item in raw_tool_calls if isinstance(item, dict)]
            if isinstance(raw_tool_calls, list)
            and all(isinstance(item, dict) for item in raw_tool_calls)
            else None
        )
        raw_tool_call_id = raw.get("tool_call_id")
        response = MessageResponse(
            id=message_id,
            runtime_message_id=runtime_id,
            conversation_id=conversation.id,
            role=_message_role(raw.get("type")),
            content=_text_content(raw.get("content")),
            tool_calls=tool_calls,
            tool_call_id=raw_tool_call_id if isinstance(raw_tool_call_id, str) else None,
            created_at=created_at,
            parent_id=previous_id,
            branch_checkpoint_id=(
                sibling_pairs[-1][1] if sibling_pairs else conversation.active_branch_checkpoint_id
            ),
            siblings=[pair[0] for pair in sibling_pairs] if len(sibling_pairs) > 1 else [],
            sibling_checkpoint_ids=(
                [pair[1] for pair in sibling_pairs] if len(sibling_pairs) > 1 else []
            ),
            branch_index=(
                next(
                    (
                        position
                        for position, pair in enumerate(sibling_pairs)
                        if pair[0] == message_id
                    ),
                    0,
                )
                if len(sibling_pairs) > 1
                else None
            ),
            branch_total=len(sibling_pairs) if len(sibling_pairs) > 1 else None,
        )
        for key in linked_keys:
            if key in feedback:
                response.feedback = MessageFeedbackBrief(rating=feedback[key])
            if key in attachments:
                response.attachments = attachments[key]
            if key in artifacts:
                response.artifacts = artifacts[key]
        responses.append(response)
        previous_id = message_id
    return responses


def _message_role(value: JsonValue) -> Literal["user", "assistant", "system", "tool"]:
    roles: dict[str, Literal["user", "assistant", "system", "tool"]] = {
        "human": "user",
        "user": "user",
        "ai": "assistant",
        "assistant": "assistant",
        "system": "system",
        "tool": "tool",
    }
    return roles.get(value, "assistant") if isinstance(value, str) else "assistant"


def _text_content(value: JsonValue) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


__all__ = ["RetainedMessageReadRequest", "load_retained_messages"]
