"""Conversation CRUD, keyset pagination cursors, and titles.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. Task 10 I4 moved execution preparation to ``chat_execution_preparation``
and keyset pagination to ``conversation_pages``; both are re-exported here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased, contains_eager

from app.agent_runtime.runtime_policy import (
    SKILL_BUILDER_RUNTIME_POLICY,
    RuntimePolicySnapshotError,
    runtime_policy_to_json,
)
from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    load_agent_tree_for_execution,
)
from app.authz.conversation_scope import load_private_conversation, lock_private_conversation
from app.authz.error_codes import agent_archived
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import conversation_not_found
from app.exceptions import AppError, ConflictError
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
from app.schemas.conversation_scope import (
    ConversationPolicyTarget,
    ConversationPromotion,
    DraftConversationCleanup,
)
from app.services.chat.chat_execution_preparation import (
    conversation_title_from_content,
    create_conversation,
    prepare_executable_conversation,
    prepare_resumed_conversation,
    promote_draft_conversation,
    require_executable_conversation,
)
from app.services.chat.conversation_mutations import (
    clear_active_branch_override,
    mark_conversation_read,
    maybe_set_auto_title,
    set_active_branch,
    touch_conversation,
    update_conversation,
)
from app.services.chat.conversation_pages import (
    ConversationCursorScope,
    ConversationPageCursor,
    _conversation_sort_column,
    _conversation_sort_value,
    _decode_conversation_cursor,
    _encode_conversation_cursor,
    _escape_like,
    list_conversations_page,
    list_global_conversations_page,
)
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

logger = logging.getLogger(__name__)

# Compatibility re-exports: names this module exposed before the I4 split.
__all__ = ["clear_active_branch_override", "mark_conversation_read", "maybe_set_auto_title"]
__all__ += ["set_active_branch", "touch_conversation", "update_conversation"]
__all__ += ["create_conversation", "prepare_executable_conversation"]
__all__ += ["prepare_resumed_conversation"]
__all__ += ["require_executable_conversation", "ConversationCursorScope", "ConversationPageCursor"]
__all__ += ["_conversation_sort_column", "_conversation_sort_value", "_decode_conversation_cursor"]
__all__ += ["_encode_conversation_cursor", "_escape_like", "list_conversations_page"]
__all__ += ["list_global_conversations_page", "ensure_conversation_runtime_policy"]
__all__ += ["conversation_title_from_content", "promote_draft_conversation", "and_", "func"]
__all__ += ["InstrumentedAttribute", "contains_eager", "SKILL_BUILDER_RUNTIME_POLICY"]
__all__ += ["RuntimePolicySnapshotError", "runtime_policy_to_json", "AgentExecutionTarget"]
__all__ += ["load_agent_tree_for_execution", "agent_archived", "ConflictError", "AppError"]
__all__ += ["AGENT_RUNTIME_PROFILE_SKILL_BUILDER", "AGENT_RUNTIME_PROFILE_STANDARD"]
__all__ += ["ConversationSort", "ConversationPolicyTarget", "ConversationPromotion", "or_"]


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
