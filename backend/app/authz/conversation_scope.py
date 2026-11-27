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
from app.authz.published_conversation import PublishedConversationContext
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import agent_not_found, conversation_not_found
from app.exceptions import AppError
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation


async def readable_conversation(
    db: AsyncSession, conversation: Conversation | None, context: OrgContext
) -> Conversation | None:
    """Check a background child's live delegation after its normal ownership lookup."""
    if conversation is None:
        return None
    if isinstance(context, PublishedConversationContext):
        return (
            conversation
            if (
                conversation.id == context.published_conversation_id
                and conversation.user_id == context.user_id
                and conversation.org_id == context.org_id
                and conversation.tenant_id == context.tenant_id
            )
            else None
        )
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        from app.services.independent_job_runs import can_read_independent_child_conversation

        if not await can_read_independent_child_conversation(
            db, conversation_id=conversation.id, context=context
        ):
            return None
    return conversation


async def can_read_conversation_run_id(
    db: AsyncSession, conversation: Conversation, run_id: UUID, context: OrgContext
) -> bool:
    """Additional lineage proof required for run-addressed reads of a background child."""
    if conversation.source != INDEPENDENT_JOB_SOURCE:
        return True
    from app.services.independent_job_runs import can_read_independent_child_run

    return await can_read_independent_child_run(
        db, conversation_id=conversation.id, run_id=run_id, context=context
    )


async def load_private_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation | None:
    await require_private_actor(db, context)
    row = await db.scalar(
        private_scoped(
            select(Conversation).where(Conversation.id == conversation_id),
            Conversation,
            context,
        )
    )
    return await readable_conversation(db, row, context)


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
    from app.authz.organization_execution_gateway import held_conversation

    held = held_conversation(db, conversation_id)
    if held is not None:
        return held
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
    row = await readable_conversation(db, row, context)
    if row is None:
        raise conversation_not_found()
    return row


async def _require_current_actor(db: AsyncSession, context: OrgContext) -> None:
    try:
        await require_private_actor(db, context)
    except AppError:
        raise conversation_not_found() from None
