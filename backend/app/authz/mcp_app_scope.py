"""Persisted MCP App runs inherit an exact private Conversation and Agent scope."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding


@dataclass(frozen=True, slots=True)
class McpAppRunTarget:
    conversation_id: UUID
    run_id: UUID


@dataclass(frozen=True, slots=True)
class McpAppBindingTarget:
    conversation_id: UUID
    run_id: UUID
    tool_call_id: str


def private_mcp_app_binding_query(
    target: McpAppBindingTarget, context: OrgContext
) -> Select[tuple[McpAppInvocationBinding]]:
    return (
        select(McpAppInvocationBinding)
        .execution_options(autoflush=False, populate_existing=True)
        .where(
            McpAppInvocationBinding.conversation_id == target.conversation_id,
            McpAppInvocationBinding.run_id == target.run_id,
            McpAppInvocationBinding.tool_call_id == target.tool_call_id,
            McpAppInvocationBinding.user_id == context.user_id,
        )
    )


async def load_private_mcp_app_run(
    db: AsyncSession, target: McpAppRunTarget, context: OrgContext
) -> ConversationRun | None:
    with db.no_autoflush:
        try:
            conversation = await require_private_conversation(db, target.conversation_id, context)
        except AppError:
            return None
        if conversation.agent_id is None:
            return None
        agent_id = await db.scalar(
            select(Agent.id).where(
                Agent.id == conversation.agent_id, private_scope_predicate(Agent, context)
            )
        )
        if agent_id is None:
            return None
        return await db.scalar(
            select(ConversationRun).where(
                ConversationRun.id == target.run_id,
                ConversationRun.conversation_id == conversation.id,
                ConversationRun.agent_id == agent_id,
                ConversationRun.user_id == context.user_id,
            )
        )
