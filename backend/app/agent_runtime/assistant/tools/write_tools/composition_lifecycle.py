"""Lifecycle lookup shared by Assistant composition writers."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import load_agent_in_scope
from app.agent_runtime.assistant.tools.write_tools.context import WriteToolContext
from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    authorize_agent_root_mutation,
    load_agent_tree_for_execution,
)
from app.authz.assistant_scope import WRITE, assistant_target_scope
from app.models.agent import Agent


async def get_executable_agent(ctx: WriteToolContext, session: AsyncSession) -> Agent | None:
    """Load an active mutable root while serializing its complete persisted tree.

    The actor needs ``can_edit`` (plan 11.7); an allowed non-owner mutates the tree
    in its owner's scope. Legacy keeps the actor's own scope and query order.
    """
    scope = await assistant_target_scope(session, ctx.org_context, ctx.agent_id, WRITE)
    if scope is None:
        return None
    await authorize_agent_root_mutation(session, AgentExecutionTarget(ctx.agent_id, scope))
    return await load_agent_in_scope(session, ctx.agent_id, scope, viewer=ctx.org_context)


async def require_executable_agent(
    ctx: WriteToolContext,
    session: AsyncSession,
    agent_id: uuid.UUID,
) -> None:
    """Require a selected child Agent tree before adding its link."""
    await load_agent_tree_for_execution(
        session,
        AgentExecutionTarget(agent_id, ctx.org_context),
    )
