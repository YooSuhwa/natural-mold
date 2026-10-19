"""Conversation keyset pagination cursors and page queries (Task 10 I4 split).

Moved unchanged from ``app.services.chat.conversations``, which re-exports every name.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, assert_never

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, contains_eager

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationSort

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
    payload: dict[str, str | bool] = {
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
    context: OrgContext,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    await require_private_actor(db, context)
    timestamp_column = _conversation_sort_column(sort)
    query = select(Conversation).where(
        Conversation.agent_id == agent_id,
        Conversation.source == "ui",
        private_scope_predicate(Conversation, context),
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
    context: OrgContext,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    await require_private_actor(db, context)
    timestamp_column = _conversation_sort_column(sort)
    query = (
        select(Conversation)
        .outerjoin(
            Agent,
            and_(Conversation.agent_id == Agent.id, private_scope_predicate(Agent, context)),
        )
        .where(
            private_scope_predicate(Conversation, context),
            # 히든 런타임 에이전트(스킬 빌더 등)의 대화는 네비게이터/최근 대화에
            # 노출하지 않는다 — 빌더 라우트가 전용 진입점이다.
            or_(Agent.id.is_(None), Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD),
            Conversation.source == "ui",
        )
        .options(contains_eager(Conversation.agent))
        .execution_options(populate_existing=True)
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
