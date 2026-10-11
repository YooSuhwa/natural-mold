"""Private conversation scope is independent of its agent's owner or existence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import agent_not_found, conversation_not_found
from app.exceptions import AppError
from app.models.conversation import Conversation


async def load_private_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation | None:
    await require_private_actor(db, context)
    return await db.scalar(
        private_scoped(
            select(Conversation).where(Conversation.id == conversation_id),
            Conversation,
            context,
        )
    )


async def resolve_conversation_actor(
    db: AsyncSession, conversation_id: UUID, user_id: UUID
) -> OrgContext:
    """Rehydrate an authorized target's persisted scope, never the user's preference."""
    conversation = (
        await db.execute(
            select(Conversation.user_id, Conversation.org_id, Conversation.tenant_id)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if conversation is None:
        raise conversation_not_found()
    return await resolve_private_actor(
        db,
        PrivateResourceActor(
            user_id=conversation[0],
            org_id=conversation[1],
            tenant_id=conversation[2],
        ),
    )


def require_conversation_agent_id(conversation: Conversation) -> UUID:
    """Preserved history stays readable; an agentless history cannot start a run."""
    if conversation.agent_id is None:
        raise agent_not_found()
    return conversation.agent_id


async def lock_private_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation:
    await _require_current_actor(db, context)
    row = await db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, private_scope_predicate(Conversation, context))
        .with_for_update(of=Conversation)
        .execution_options(populate_existing=True, autoflush=False)
    )
    if row is None:
        raise conversation_not_found()
    return row


async def require_private_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation:
    await _require_current_actor(db, context)
    row = await db.scalar(
        private_scoped(
            select(Conversation).where(Conversation.id == conversation_id), Conversation, context
        ).execution_options(populate_existing=True, autoflush=False)
    )
    if row is None:
        raise conversation_not_found()
    return row


async def _require_current_actor(db: AsyncSession, context: OrgContext) -> None:
    try:
        await require_private_actor(db, context)
    except AppError:
        raise conversation_not_found() from None
