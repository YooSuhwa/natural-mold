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

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased, contains_eager

from app.agent_runtime.runtime_policy import (
    SKILL_BUILDER_RUNTIME_POLICY,
    runtime_policy_to_json,
)
from app.authz.conversation_scope import load_private_conversation, lock_private_conversation
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import agent_not_found, conversation_not_found
from app.models.agent import (
    AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
    AGENT_RUNTIME_PROFILE_STANDARD,
    Agent,
)
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_event import MessageEvent
from app.schemas.conversation import ConversationSort
from app.schemas.conversation_scope import ConversationPromotion, DraftConversationCleanup
from app.services.chat.conversation_mutations import (
    clear_active_branch_override as clear_active_branch_override,
)
from app.services.chat.conversation_mutations import (
    mark_conversation_read as mark_conversation_read,
)
from app.services.chat.conversation_mutations import (
    maybe_set_auto_title as maybe_set_auto_title,
)
from app.services.chat.conversation_mutations import (
    set_active_branch as set_active_branch,
)
from app.services.chat.conversation_mutations import (
    touch_conversation as touch_conversation,
)
from app.services.chat.conversation_mutations import (
    update_conversation as update_conversation,
)

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


async def list_conversations(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> list[Conversation]:
    await require_private_actor(db, context)
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.agent_id == agent_id,
            Conversation.source == "ui",
            private_scope_predicate(Conversation, context),
        )
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def is_agent_owned_by_user(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> bool:
    await require_private_actor(db, context)
    result = await db.execute(
        select(Agent.id)
        .where(Agent.id == agent_id, private_scope_predicate(Agent, context))
        .limit(1)
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


async def create_conversation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    title: str | None = None,
    *,
    context: OrgContext,
    source: str = "ui",
) -> Conversation:
    await require_private_actor(db, context)
    agent = await db.scalar(
        private_scoped(select(Agent).where(Agent.id == agent_id), Agent, context)
    )
    if agent is None:
        raise agent_not_found()
    conv = Conversation(
        agent_id=agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
        title=title or "새 대화",
        source=source,
    )
    if source == "draft":
        runtime_profile = await db.scalar(select(Agent.runtime_profile).where(Agent.id == agent_id))
        if runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER:
            conv.runtime_policy_snapshot = runtime_policy_to_json(
                SKILL_BUILDER_RUNTIME_POLICY.effective
            )
            conv.runtime_policy_version = SKILL_BUILDER_RUNTIME_POLICY.effective.version
            conv.runtime_policy_hash = SKILL_BUILDER_RUNTIME_POLICY.policy_hash
            conv.runtime_policy_source = SKILL_BUILDER_RUNTIME_POLICY.source
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return conv


async def promote_draft_conversation(
    db: AsyncSession, target: ConversationPromotion
) -> Conversation:
    conv = await lock_private_conversation(db, target.conversation_id, target.context)
    if conv.source != "draft":
        return conv
    conv.source = "ui"
    if target.title_from_content:
        conv.title = conversation_title_from_content(target.title_from_content)
    await db.flush()
    await db.refresh(conv)
    return conv


async def gc_orphan_draft_conversations(db: AsyncSession, cleanup: DraftConversationCleanup) -> int:
    """Delete abandoned, message-less draft conversations past the cutoff.

    A draft (``source == "draft"``) is created by ``POST
    .../conversations/draft`` and only flips to ``"ui"`` via
    :func:`promote_draft_conversation` when the user sends a first message.
    A draft abandoned before sending is invisible to the UI (the list filters
    ``source == "ui"``) and never deleted, so empty drafts accumulate.

    This removes rows that are **both**:

    * still ``source == "draft"`` (never promoted — promotion is the
      first-message signal, so a non-draft row is never touched), AND
    * message-less: no recorded turn, durable run/input or side-conversation
      child exists. Durable work is preserved even before its first event.

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
    retention_hours = cleanup.retention_hours
    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    has_event = (
        select(MessageEvent.id).where(MessageEvent.conversation_id == Conversation.id).exists()
    )
    side_child = aliased(Conversation)
    has_side_child = (
        select(side_child.id)
        .where(side_child.side_chat_parent_id == Conversation.id)
        .correlate(Conversation)
        .exists()
    )
    has_run = (
        select(ConversationRun.id)
        .where(ConversationRun.conversation_id == Conversation.id)
        .exists()
    )
    has_input = (
        select(ConversationRunInput.id)
        .where(ConversationRunInput.conversation_id == Conversation.id)
        .exists()
    )
    result = await db.execute(
        delete(Conversation).where(
            Conversation.source == "draft",
            Conversation.created_at < cutoff,
            ~has_event,
            ~has_side_child,
            ~has_run,
            ~has_input,
        )
    )
    await db.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    if deleted:
        logger.info(
            "Draft conversation GC: deleted %d empty draft(s) older than %s under %s",
            deleted,
            cutoff.isoformat(),
            cleanup.scope,
        )
    return deleted


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> Conversation | None:
    return await load_private_conversation(db, conversation_id, context)


async def get_owned_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> Conversation | None:
    """The private conversation boundary is independent of its agent owner."""
    return await load_private_conversation(db, conversation_id, context)


async def get_owned_ui_conversation_with_agent(
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> Conversation | None:
    await require_private_actor(db, context)
    result = await db.execute(
        private_scoped(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.source == "ui"
            ),
            Conversation,
            context,
        )
        .outerjoin(
            Agent,
            and_(Conversation.agent_id == Agent.id, private_scope_predicate(Agent, context)),
        )
        .options(contains_eager(Conversation.agent))
        .execution_options(populate_existing=True)
    )
    return result.unique().scalar_one_or_none()


async def delete_conversation(db: AsyncSession, conv: Conversation, context: OrgContext) -> None:
    from app.agent_runtime.checkpointer import delete_thread
    from app.agent_runtime.offload_storage import delete_conversation_offloads
    from app.agent_runtime.runtime_config import _DATA_DIR

    locked_conversation = await lock_private_conversation(db, conv.id, context)
    foreign_child_id = await db.scalar(
        select(Conversation.id)
        .where(
            Conversation.side_chat_parent_id == locked_conversation.id,
            ~private_scope_predicate(Conversation, context),
        )
        .limit(1)
    )
    if foreign_child_id is not None:
        raise conversation_not_found()
    active_run_id = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.conversation_id == locked_conversation.id,
            ConversationRun.is_active.is_(True),
        )
        .limit(1)
    )
    if active_run_id is not None:
        raise HTTPException(status_code=409, detail="Conversation has an active run")

    owner_id = locked_conversation.user_id
    await delete_thread(str(locked_conversation.id))
    delete_conversation_offloads(
        _DATA_DIR,
        owner_id=str(owner_id),
        conversation_id=str(locked_conversation.id),
    )
    # Keep retained side history discoverable when its parent is deliberately deleted.
    await db.execute(
        update(Conversation)
        .where(
            Conversation.side_chat_parent_id == locked_conversation.id,
            Conversation.source == "side_chat",
            private_scope_predicate(Conversation, context),
        )
        .values(source="ui", side_chat_parent_id=None)
    )
    await db.delete(locked_conversation)
    await db.flush()
