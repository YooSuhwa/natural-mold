"""Conversation CRUD, keyset pagination cursors, and titles.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, assert_never

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, contains_eager

from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.schemas.conversation import ConversationSort, ConversationUpdate

logger = logging.getLogger(__name__)


def conversation_title_from_content(content: str) -> str:
    title = content.strip().replace("\n", " ")
    if not title:
        return "새 대화"
    if len(title) > 40:
        return title[:37] + "..."
    return title


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


async def list_conversations(db: AsyncSession, agent_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id, Conversation.source == "ui")
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def is_agent_owned_by_user(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Agent.id).where(Agent.id == agent_id, Agent.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


ConversationCursorScope = Literal["agent", "global"]


@dataclass(frozen=True, slots=True)
class ConversationPageCursor:
    scope: ConversationCursorScope
    sort: ConversationSort
    timestamp: datetime
    id: uuid.UUID
    is_pinned: bool | None = None


def _escape_like(term: str) -> str:
    """LIKE 메타문자(``\\``, ``%``, ``_``)를 리터럴로 이스케이프한다 (escape="\\\\"와 짝)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conversation_sort_column(sort: ConversationSort) -> InstrumentedAttribute[datetime]:
    match sort:
        case "updated":
            return Conversation.updated_at
        case "created":
            return Conversation.created_at
        case unreachable:
            assert_never(unreachable)


def _conversation_sort_value(conversation: Conversation, sort: ConversationSort) -> datetime:
    match sort:
        case "updated":
            return conversation.updated_at
        case "created":
            return conversation.created_at
        case unreachable:
            assert_never(unreachable)


def _encode_conversation_cursor(
    conversation: Conversation,
    *,
    scope: ConversationCursorScope,
    sort: ConversationSort,
) -> str:
    payload = {
        "scope": scope,
        "sort": sort,
        "timestamp": _conversation_sort_value(conversation, sort).isoformat(),
        "id": str(conversation.id),
    }
    if scope == "agent":
        payload["is_pinned"] = bool(conversation.is_pinned)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_conversation_cursor(
    cursor: str,
    *,
    expected_scope: ConversationCursorScope,
    expected_sort: ConversationSort,
) -> ConversationPageCursor:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        scope = payload["scope"]
        sort = payload["sort"]
        if scope != expected_scope or sort != expected_sort:
            raise ValueError("conversation cursor scope or sort mismatch")
        is_pinned = bool(payload["is_pinned"]) if scope == "agent" else None
        timestamp = datetime.fromisoformat(str(payload["timestamp"]))
        if timestamp.tzinfo is not None:
            # DB 컬럼은 naive UTC — aware 커서는 UTC로 환산 후 naive로 정규화
            timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
        return ConversationPageCursor(
            scope=expected_scope,
            sort=expected_sort,
            timestamp=timestamp,
            id=uuid.UUID(str(payload["id"])),
            is_pinned=is_pinned,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid conversation cursor") from exc


async def list_conversations_page(
    db: AsyncSession,
    agent_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    timestamp_column = _conversation_sort_column(sort)
    query = select(Conversation).where(
        Conversation.agent_id == agent_id,
        Conversation.source == "ui",
    )

    search = (q or "").strip()
    if search:
        query = query.where(
            func.lower(func.coalesce(Conversation.title, "")).like(
                f"%{_escape_like(search.lower())}%", escape="\\"
            )
        )

    if cursor:
        page_cursor = _decode_conversation_cursor(
            cursor,
            expected_scope="agent",
            expected_sort=sort,
        )
        if page_cursor.is_pinned is None:
            raise ValueError("agent conversation cursor missing pin state")
        same_bucket_after = and_(
            Conversation.is_pinned == page_cursor.is_pinned,
            or_(
                timestamp_column < page_cursor.timestamp,
                and_(timestamp_column == page_cursor.timestamp, Conversation.id < page_cursor.id),
            ),
        )
        if page_cursor.is_pinned:
            query = query.where(or_(Conversation.is_pinned.is_(False), same_bucket_after))
        else:
            query = query.where(same_bucket_after)

    result = await db.execute(
        query.order_by(
            Conversation.is_pinned.desc(),
            timestamp_column.desc(),
            Conversation.id.desc(),
        ).limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        _encode_conversation_cursor(items[-1], scope="agent", sort=sort)
        if has_more and items
        else None
    )
    return items, next_cursor, has_more


async def list_global_conversations_page(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    timestamp_column = _conversation_sort_column(sort)
    query = (
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(
            Agent.user_id == user_id,
            # 히든 런타임 에이전트(스킬 빌더 등)의 대화는 네비게이터/최근 대화에
            # 노출하지 않는다 — 빌더 라우트가 전용 진입점이다.
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            Conversation.source == "ui",
        )
        .options(contains_eager(Conversation.agent))
    )

    search = (q or "").strip()
    if search:
        query = query.where(
            func.lower(func.coalesce(Conversation.title, "")).like(
                f"%{_escape_like(search.lower())}%", escape="\\"
            )
        )

    if cursor:
        page_cursor = _decode_conversation_cursor(
            cursor,
            expected_scope="global",
            expected_sort=sort,
        )
        query = query.where(
            or_(
                timestamp_column < page_cursor.timestamp,
                and_(timestamp_column == page_cursor.timestamp, Conversation.id < page_cursor.id),
            )
        )

    result = await db.execute(
        query.order_by(timestamp_column.desc(), Conversation.id.desc()).limit(limit + 1)
    )
    rows = list(result.unique().scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        _encode_conversation_cursor(items[-1], scope="global", sort=sort)
        if has_more and items
        else None
    )
    return items, next_cursor, has_more


async def create_conversation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    title: str | None = None,
    *,
    source: str = "ui",
) -> Conversation:
    conv = Conversation(agent_id=agent_id, title=title or "새 대화", source=source)
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return conv


async def promote_draft_conversation(
    db: AsyncSession,
    conv: Conversation,
    *,
    title_from_content: str | None = None,
) -> Conversation:
    if conv.source != "draft":
        return conv
    conv.source = "ui"
    if title_from_content:
        conv.title = conversation_title_from_content(title_from_content)
    await db.flush()
    await db.refresh(conv)
    return conv


async def gc_orphan_draft_conversations(db: AsyncSession, *, retention_hours: int) -> int:
    """Delete abandoned, message-less draft conversations past the cutoff.

    A draft (``source == "draft"``) is created by ``POST
    .../conversations/draft`` and only flips to ``"ui"`` via
    :func:`promote_draft_conversation` when the user sends a first message.
    A draft abandoned before sending is invisible to the UI (the list filters
    ``source == "ui"``) and never deleted, so empty drafts accumulate.

    This removes rows that are **both**:

    * still ``source == "draft"`` (never promoted — promotion is the
      first-message signal, so a non-draft row is never touched), AND
    * message-less: no ``message_events`` turn row exists for the
      conversation (the ORM-visible proxy for a recorded assistant turn).

    The age check uses ``created_at`` (naive UTC, matching the column) so a
    just-opened draft the user is still typing into is never collected. Child
    rows (message_events, attachments, runs, share links, ...) are all
    ``ON DELETE CASCADE``, so the row delete is self-contained. Commits the
    transaction so the cron caller doesn't have to manage one. Returns the
    number of drafts deleted.
    """

    # Reject (rather than clamp) a non-positive retention. ``retention_hours == 0``
    # sets ``cutoff = now`` and would delete a draft the user opened moments ago (still
    # typing), so a mis-set ``0`` must surface loudly as a config error instead of
    # silently destroying live drafts or silently substituting a value the operator
    # never chose.
    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    has_event = (
        select(MessageEvent.id).where(MessageEvent.conversation_id == Conversation.id).exists()
    )
    result = await db.execute(
        delete(Conversation).where(
            Conversation.source == "draft",
            Conversation.created_at < cutoff,
            ~has_event,
        )
    )
    await db.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    if deleted:
        logger.info(
            "Draft conversation GC: deleted %d orphan draft(s) older than %s",
            deleted,
            cutoff.isoformat(),
        )
    return deleted


async def get_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    return result.scalar_one_or_none()


async def get_owned_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Conversation lookup gated by ownership through Agent.user_id.

    Single SELECT joining ``conversations -> agents`` so callers don't have to
    issue two queries (conversation, then agent ownership check). Returns
    ``None`` when the conversation doesn't exist *or* belongs to another user
    — callers should map both to ``conversation_not_found`` so existence
    isn't leaked via 403/404 differences.
    """
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(Conversation.id == conversation_id)
        .where(Agent.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_owned_ui_conversation_with_agent(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(
            Conversation.id == conversation_id,
            Conversation.source == "ui",
            Agent.user_id == user_id,
        )
        .options(contains_eager(Conversation.agent))
    )
    return result.unique().scalar_one_or_none()


async def update_conversation(
    db: AsyncSession, conv: Conversation, data: ConversationUpdate
) -> Conversation:
    if data.title is not None:
        conv.title = data.title
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    await db.flush()
    await db.refresh(conv)
    return conv


async def mark_conversation_read(db: AsyncSession, conv: Conversation) -> Conversation:
    conv.unread_count = 0
    conv.last_read_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    from app.agent_runtime.checkpointer import delete_thread

    await delete_thread(str(conv.id))
    await db.delete(conv)
    await db.flush()


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
    title = conversation_title_from_content(content)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
        .values(title=title)
    )
    await db.flush()


async def touch_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Bump ``conversation.updated_at`` to anchor message-list timestamps."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            updated_at=datetime.now(UTC).replace(tzinfo=None),
            last_activity_source="user",
        )
    )
    await db.flush()


async def clear_active_branch_override(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Reset ``active_branch_checkpoint_id`` so the next list call falls back
    to the newest leaf — used after edit/regenerate where the new branch is
    the most recent and should automatically become active."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(active_branch_checkpoint_id=None)
    )
    await db.flush()
