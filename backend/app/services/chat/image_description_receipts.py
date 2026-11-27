"""Claim one billable image description and recover the same result on replay.

Claim and completion each own a short transaction. Callers reauthorize the
current model/egress policy before using a receipt. An abandoned claim stays
pending because there is no provider guarantee that the first call did not run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.chat_image_description import ChatImageDescription
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment

type ResultStatus = Literal["acquired", "pending", "completed", "failed", "unavailable"]


@dataclass(frozen=True, slots=True)
class DescriptionReservation:
    status: ResultStatus
    receipt_id: UUID | None = None
    description: str | None = None
    failure_code: str | None = None


_UNAVAILABLE = DescriptionReservation("unavailable")
_SHA256 = re.compile("[0-9a-f]{64}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _active_actor(db: AsyncSession, context: OrgContext) -> bool:
    try:
        await require_private_actor(db, context)
    except AppError as error:
        if error.status not in (403, 404):
            raise
        return False
    return True


def _scoped_receipts(context: OrgContext) -> Select[tuple[ChatImageDescription]]:
    return (
        select(ChatImageDescription)
        .join(Conversation, Conversation.id == ChatImageDescription.conversation_id)
        .join(MessageAttachment, MessageAttachment.id == ChatImageDescription.attachment_id)
        .join(ConversationRun, ConversationRun.id == ChatImageDescription.run_id)
        .where(
            private_scope_predicate(Conversation, context),
            private_scope_predicate(MessageAttachment, context),
            MessageAttachment.conversation_id == Conversation.id,
            MessageAttachment.mime_type.startswith("image/"),
            or_(
                MessageAttachment.content_sha256.is_(None),
                MessageAttachment.content_sha256 == ChatImageDescription.content_sha256,
            ),
            ConversationRun.conversation_id == Conversation.id,
            ConversationRun.user_id == context.user_id,
        )
    )


async def find_description(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    attachment_id: UUID,
    content_sha256: str,
    model_id: UUID,
    policy_key: str,
    question_sha256: str,
) -> DescriptionReservation:
    """Read a pinned receipt. Old uploads need a freshly verified digest from the caller."""
    if (
        not policy_key
        or not _SHA256.fullmatch(content_sha256)
        or not _SHA256.fullmatch(question_sha256)
    ):
        return _UNAVAILABLE
    if not await _active_actor(db, context):
        return _UNAVAILABLE
    row = await db.scalar(
        _scoped_receipts(context)
        .where(
            ChatImageDescription.conversation_id == conversation_id,
            ChatImageDescription.attachment_id == attachment_id,
            ChatImageDescription.content_sha256 == content_sha256,
            ChatImageDescription.model_id == model_id,
            ChatImageDescription.policy_sha256 == _digest(policy_key),
            ChatImageDescription.question_sha256 == question_sha256,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return _UNAVAILABLE
    if row.status == "completed":
        return DescriptionReservation("completed", row.id, description=row.description)
    if row.status == "failed":
        return DescriptionReservation("failed", row.id, failure_code=row.failure_code)
    return DescriptionReservation("pending", row.id)


async def reserve_description(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_id: UUID,
    attachment_id: UUID,
    content_sha256: str,
    question: str,
    model_id: UUID,
    policy_key: str,
) -> DescriptionReservation:
    """Only the owner of the first durable claim may start an external request."""
    if (
        not policy_key
        or not _SHA256.fullmatch(content_sha256)
        or not await _active_actor(db, context)
    ):
        return _UNAVAILABLE
    source = await db.scalar(
        select(MessageAttachment.id)
        .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
        .join(ConversationRun, ConversationRun.conversation_id == Conversation.id)
        .where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.mime_type.startswith("image/"),
            or_(
                MessageAttachment.content_sha256.is_(None),
                MessageAttachment.content_sha256 == content_sha256,
            ),
            private_scope_predicate(MessageAttachment, context),
            Conversation.id == conversation_id,
            private_scope_predicate(Conversation, context),
            ConversationRun.id == run_id,
            ConversationRun.status.in_(("queued", "running")),
            ConversationRun.cancel_requested_at.is_(None),
            ConversationRun.user_id == context.user_id,
        )
        .with_for_update(of=MessageAttachment)
    )
    if source is None:
        return _UNAVAILABLE
    question_sha256 = _digest(question)
    key = {
        "attachment_id": attachment_id,
        "conversation_id": conversation_id,
        "content_sha256": content_sha256,
        "model_id": model_id,
        "policy_sha256": _digest(policy_key),
        "question_sha256": question_sha256,
    }
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    receipt_id = await db.scalar(
        insert(ChatImageDescription)
        .values(**key, run_id=run_id, status="pending")
        .on_conflict_do_nothing(index_elements=list(key))
        .returning(ChatImageDescription.id)
    )
    await db.commit()
    if receipt_id is not None:
        return DescriptionReservation("acquired", receipt_id)
    return await find_description(
        db,
        context=context,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
        content_sha256=content_sha256,
        model_id=model_id,
        policy_key=policy_key,
        question_sha256=question_sha256,
    )


async def finish_description(
    db: AsyncSession,
    *,
    context: OrgContext,
    receipt_id: UUID,
    run_id: UUID,
    description: str | None = None,
    failure_code: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: Decimal | None = None,
    credential_scope: Literal["personal", "tenant"] | None = None,
) -> bool:
    """Finalize once, for the owning run. Never persist raw provider errors."""
    if (description is None) == (failure_code is None):
        raise ValueError("Specify exactly one outcome for an image description")
    if description is not None and (
        not description.strip() or len(description) > 8000 or "\x00" in description
    ):
        raise ValueError("Invalid image description")
    if failure_code is not None and re.fullmatch("[a-z][a-z0-9_]{0,63}", failure_code) is None:
        raise ValueError("Invalid image description failure code")
    if not await _active_actor(db, context):
        return False
    allowed = (
        _scoped_receipts(context)
        .with_only_columns(ChatImageDescription.id)
        .where(
            ChatImageDescription.id == receipt_id,
            ChatImageDescription.run_id == run_id,
        )
    )
    changed = await db.scalar(
        update(ChatImageDescription)
        .where(ChatImageDescription.id.in_(allowed), ChatImageDescription.status == "pending")
        .values(
            status="completed" if description is not None else "failed",
            description=description,
            failure_code=failure_code,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            credential_scope=credential_scope,
        )
        .returning(ChatImageDescription.id)
    )
    await db.commit()
    return changed is not None
