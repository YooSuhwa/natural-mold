"""Run-path agent loads that admit shared agents (plan 11.0, 11.1; F-06)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.run_agent_loader import load_shared_run_agent
from app.models.agent import Agent
from app.services.chat.runtime_context import _agent_runtime_load_options, get_agent_with_tools


async def get_run_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> Agent | None:
    """``get_agent_with_tools`` (owner fast path), else someone else's agent of this
    organization the caller may run once ``agent`` is enforced."""
    agent = await get_agent_with_tools(db, agent_id, context)
    if agent is not None:
        return agent
    return await load_shared_run_agent(db, agent_id, context, _agent_runtime_load_options())


__all__ = ["get_run_agent_with_tools"]
