"""Authorize the persisted conversation and current membership before private writes."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import lock_private_conversation
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.schemas.conversation_scope import (
    ConversationAutoTitle,
    ConversationBranchMutation,
    ConversationMutation,
)


async def update_conversation(db: AsyncSession, mutation: ConversationMutation) -> Conversation:
    row = await lock_private_conversation(db, mutation.conversation_id, mutation.context)
    if mutation.changes.title is not None:
        row.title = mutation.changes.title
    if mutation.changes.is_pinned is not None:
        row.is_pinned = mutation.changes.is_pinned
    await db.flush()
    await db.refresh(row)
    return row


async def mark_conversation_read(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation:
    row = await lock_private_conversation(db, conversation_id, context)
    row.unread_count = 0
    row.last_read_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()
    await db.refresh(row)
    return row


async def touch_conversation(db: AsyncSession, conversation_id: UUID, context: OrgContext) -> None:
    row = await lock_private_conversation(db, conversation_id, context)
    activity_at = datetime.now(UTC)
    row.updated_at = activity_at.replace(tzinfo=None)
    row.last_activity_at = activity_at
    row.last_activity_source = "user"
    await db.flush()


async def maybe_set_auto_title(db: AsyncSession, target: ConversationAutoTitle) -> None:
    from app.services.chat.conversations import conversation_title_from_content

    row = await lock_private_conversation(db, target.conversation_id, target.context)
    if row.title == "새 대화":
        row.title = conversation_title_from_content(target.content)
        await db.flush()


async def clear_active_branch_override(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> None:
    await set_active_branch(db, ConversationBranchMutation(conversation_id, context, None))


async def set_active_branch(db: AsyncSession, target: ConversationBranchMutation) -> None:
    row = await lock_private_conversation(db, target.conversation_id, target.context)
    row.active_branch_checkpoint_id = target.checkpoint_id
    await db.flush()
