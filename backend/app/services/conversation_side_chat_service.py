"""Independent side-conversation creation and history visibility."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.services import chat_service
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy


async def get_or_create_side_chat(
    db: AsyncSession, parent_id: uuid.UUID
) -> tuple[Conversation, bool]:
    """Reuse or create a side chat under the owned parent's policy row lock."""
    parent, _, _ = await ensure_conversation_runtime_policy(db, parent_id)
    existing = await db.scalar(
        select(Conversation).where(
            Conversation.side_chat_parent_id == parent.id,
            Conversation.agent_id == parent.agent_id,
        )
    )
    if existing is not None:
        return existing, False
    conversation = await chat_service.create_conversation(
        db, parent.agent_id, parent.title, source="side_chat"
    )
    conversation.runtime_policy_snapshot = parent.runtime_policy_snapshot
    conversation.side_chat_parent_id = parent.id
    conversation.runtime_policy_version = parent.runtime_policy_version
    conversation.runtime_policy_hash = parent.runtime_policy_hash
    conversation.runtime_policy_source = parent.runtime_policy_source
    return conversation, True


def promote_side_chat(conversation: Conversation) -> bool:
    """Expose a side chat in regular history, reporting whether it changed."""
    if conversation.source != "side_chat":
        return False
    conversation.source = "ui"
    return True
