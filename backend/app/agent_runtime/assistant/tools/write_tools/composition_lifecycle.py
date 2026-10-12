"""Lifecycle lookup shared by Assistant composition writers."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.write_tools.context import (
    WriteToolContext,
    get_agent_with_session,
)
from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    authorize_agent_root_mutation,
    load_agent_tree_for_execution,
)
from app.models.agent import Agent


async def get_executable_agent(ctx: WriteToolContext, session: AsyncSession) -> Agent | None:
    """Load an active mutable root while serializing its complete persisted tree."""
    await authorize_agent_root_mutation(
        session,
        AgentExecutionTarget(ctx.agent_id, ctx.org_context),
    )
    return await get_agent_with_session(ctx, session)


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
