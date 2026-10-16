"""Run metadata and creation inherit the conversation's exact private boundary."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.execution_lock_cursor import ExecutionProofError, LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof, lock_row
from app.authz.organization_execution_proof import live_proof
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


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
    proof = live_proof(db)
    if proof is None:
        return None
    return await lock_row(
        db,
        proof.cursor,
        LockClass.RUN,
        target.run_id,
        select(ConversationRun)
        .where(
            ConversationRun.id == target.run_id,
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.agent_id == target.conversation.agent_id,
            private_run_predicate(target.conversation.context),
        )
        .with_for_update(),
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
    """Lock a run's Conversation through the canonical execution gateway.

    Acquires (or reuses) this transaction's execution proof covering the
    Agent tree and Conversation; authority failures keep returning ``None``.
    """
    try:
        await require_private_actor(db, target.context)
        proof = await acquire_execution_proof(
            db,
            target.context,
            root_agent_id=target.agent_id,
            conversation_ids=(target.conversation_id,),
        )
    except (AppError, ExecutionProofError):
        return None
    conversation = proof.cursor.held_row(LockClass.CONVERSATION, target.conversation_id)
    if not isinstance(conversation, Conversation) or conversation.agent_id != target.agent_id:
        return None
    agent_in_scope = await db.scalar(
        select(Agent.id)
        .where(
            Agent.id == target.agent_id,
            private_scope_predicate(Agent, target.context),
        )
        .execution_options(autoflush=False)
    )
    return conversation if agent_in_scope is not None else None
