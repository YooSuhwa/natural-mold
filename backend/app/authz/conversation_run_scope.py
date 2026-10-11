"""Run metadata and creation inherit the conversation's exact private boundary."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User


@dataclass(frozen=True, slots=True)
class RunConversationTarget:
    conversation_id: UUID
    agent_id: UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class RunExecutionTarget:
    run_id: UUID
    conversation: RunConversationTarget


async def lock_executable_run(
    db: AsyncSession, target: RunExecutionTarget
) -> ConversationRun | None:
    conversation = await lock_run_conversation(db, target.conversation)
    if conversation is None:
        return None
    return await db.scalar(
        select(ConversationRun)
        .where(
            ConversationRun.id == target.run_id,
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.agent_id == target.conversation.agent_id,
            private_run_predicate(target.conversation.context),
        )
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )


def private_run_predicate(context: OrgContext) -> ColumnElement[bool]:
    return (
        select(Conversation.id)
        .where(
            Conversation.id == ConversationRun.conversation_id,
            Conversation.user_id == ConversationRun.user_id,
            private_scope_predicate(Conversation, context),
        )
        .exists()
    )


def resume_parent_predicate(child: type[ConversationRun]) -> ColumnElement[bool]:
    return and_(
        child.parent_run_id == ConversationRun.id,
        child.conversation_id == ConversationRun.conversation_id,
        child.user_id == ConversationRun.user_id,
    )


async def lock_run_conversation(
    db: AsyncSession, target: RunConversationTarget
) -> Conversation | None:
    owner_id = await db.scalar(
        select(User.id)
        .where(User.id == target.context.user_id, User.is_active.is_(True))
        .with_for_update(read=True, key_share=True)
        .execution_options(autoflush=False)
    )
    if owner_id is None:
        return None
    try:
        await require_private_actor(db, target.context)
    except AppError:
        return None
    return await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == target.conversation_id,
            Conversation.agent_id == target.agent_id,
            private_scope_predicate(Conversation, target.context),
            select(Agent.id)
            .where(
                Agent.id == target.agent_id,
                private_scope_predicate(Agent, target.context),
            )
            .exists(),
        )
        .with_for_update(of=Conversation)
        .execution_options(populate_existing=True, autoflush=False)
    )
