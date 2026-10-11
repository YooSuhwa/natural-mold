"""Independent side-conversation creation and history visibility."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import lock_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import agent_not_found, conversation_not_found
from app.models.conversation import Conversation
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy


async def get_or_create_side_chat(
    db: AsyncSession, parent_id: uuid.UUID, context: OrgContext
) -> tuple[Conversation, bool]:
    """Reuse or create a side chat under the owned parent's policy row lock."""
    parent, _, _ = await ensure_conversation_runtime_policy(
        db, ConversationPolicyTarget(parent_id, context)
    )
    existing = await db.scalar(
        select(Conversation).where(
            Conversation.side_chat_parent_id == parent.id,
            Conversation.agent_id == parent.agent_id,
            private_scope_predicate(Conversation, context),
        )
    )
    if existing is not None:
        return existing, False
    conflicting_child = await db.scalar(
        select(Conversation.id).where(Conversation.side_chat_parent_id == parent.id)
    )
    if conflicting_child is not None:
        raise conversation_not_found()
    if parent.agent_id is None:
        raise agent_not_found()
    conversation = await chat_service.create_conversation(
        db, parent.agent_id, parent.title, context=context, source="side_chat"
    )
    conversation.runtime_policy_snapshot = parent.runtime_policy_snapshot
    conversation.side_chat_parent_id = parent.id
    conversation.runtime_policy_version = parent.runtime_policy_version
    conversation.runtime_policy_hash = parent.runtime_policy_hash
    conversation.runtime_policy_source = parent.runtime_policy_source
    return conversation, True


async def promote_side_chat(
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> bool:
    """Expose a side chat in regular history, reporting whether it changed."""
    conversation = await lock_private_conversation(db, conversation_id, context)
    if conversation.source != "side_chat":
        return False
    conversation.source = "ui"
    await db.flush()
    return True
