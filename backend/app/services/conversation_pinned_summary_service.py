from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_pinned_summary import ConversationPinnedSummary
from app.schemas.conversation_pinned_summary import (
    PINNED_SUMMARY_MAX_CHARS,
    PinnedConversationSummaryResponse,
    PinnedSummarySourceStatus,
)

MessageRole = Literal["assistant", "user", "tool", "system"]


@dataclass(frozen=True, slots=True)
class SummarySource:
    message_id: str
    role: MessageRole
    text: str
    branch_checkpoint_id: str


@dataclass(frozen=True, slots=True)
class InvalidPinnedSummarySourceError(Exception):
    reason: Literal["missing", "not_assistant", "empty"]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bounded_snapshot(text: str) -> str:
    return text[:PINNED_SUMMARY_MAX_CHARS]


def _find_source(sources: tuple[SummarySource, ...], message_id: str) -> SummarySource | None:
    return next((source for source in sources if source.message_id == message_id), None)


def resolve_source_status(
    row: ConversationPinnedSummary,
    active_sources: tuple[SummarySource, ...],
    historical_sources: tuple[SummarySource, ...],
) -> PinnedSummarySourceStatus:
    """Resolve whether the pinned immutable snapshot still matches its source."""

    active = _find_source(active_sources, row.source_message_id)
    if active is not None:
        return "current" if _text_hash(active.text.strip()) == row.source_text_hash else "changed"
    historical = _find_source(historical_sources, row.source_message_id)
    return "other_branch" if historical is not None else "deleted"


async def pin_summary(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    message_id: str,
    active_sources: tuple[SummarySource, ...],
) -> ConversationPinnedSummary:
    """Persist a bounded snapshot of an assistant message on the active branch."""

    source = _find_source(active_sources, message_id)
    if source is None:
        raise InvalidPinnedSummarySourceError("missing")
    if source.role != "assistant":
        raise InvalidPinnedSummarySourceError("not_assistant")
    normalized_text = source.text.strip()
    if not normalized_text:
        raise InvalidPinnedSummarySourceError("empty")

    await _lock_conversation(db, conversation_id)
    row = await db.get(ConversationPinnedSummary, conversation_id)
    if row is None:
        row = ConversationPinnedSummary(conversation_id=conversation_id)
        db.add(row)
    row.source_message_id = source.message_id
    row.source_branch_checkpoint_id = source.branch_checkpoint_id
    row.snapshot_text = _bounded_snapshot(normalized_text)
    row.source_text_hash = _text_hash(normalized_text)
    await db.flush()
    return row


async def get_summary(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> ConversationPinnedSummary | None:
    return await db.get(ConversationPinnedSummary, conversation_id)


async def unpin_summary(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    await _lock_conversation(db, conversation_id)
    row = await db.get(ConversationPinnedSummary, conversation_id)
    if row is not None:
        await db.delete(row)


async def _lock_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Serialize summary mutations, including the no-summary-row insert case."""

    await db.scalar(
        select(Conversation.id)
        .where(Conversation.id == conversation_id)
        .with_for_update(of=Conversation)
    )


def serialize_summary(
    row: ConversationPinnedSummary,
    active_sources: tuple[SummarySource, ...],
    historical_sources: tuple[SummarySource, ...],
) -> PinnedConversationSummaryResponse:
    return PinnedConversationSummaryResponse(
        conversation_id=row.conversation_id,
        source_message_id=row.source_message_id,
        source_branch_checkpoint_id=row.source_branch_checkpoint_id,
        snapshot_text=row.snapshot_text,
        source_status=resolve_source_status(row, active_sources, historical_sources),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
