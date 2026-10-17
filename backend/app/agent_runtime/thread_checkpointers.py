"""Plan 8.8 checkpointer gate for assistant threads, which are not conversations (I-06).

An assistant thread id is ``assistant_<agent id>[_<session>]``; it is bound to
that agent and the agent to the caller's company before the checkpointer pool is
touched (a mismatch is the same 404 as a missing agent). Builder threads use
``app.authz.builder_scope.builder_checkpointer``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import TenantScoped
from app.error_codes import agent_not_found
from app.models.agent import Agent


def _assistant_thread_of(thread_id: str, agent_id: uuid.UUID) -> bool:
    prefix = f"assistant_{agent_id}"
    return thread_id == prefix or thread_id.startswith(f"{prefix}_")


async def assistant_checkpointer[S](
    db: AsyncSession,
    thread_id: str,
    agent_id: uuid.UUID,
    ctx: TenantScoped,
    *,
    saver: Callable[[], S],
) -> S:
    """The checkpointer for an assistant thread of an agent in ``ctx``'s company.

    Edit access to the agent is the caller's own check; this only binds the
    thread to that agent and the agent to the caller's company.
    """
    tenant_id = await db.scalar(
        select(Agent.tenant_id).where(Agent.id == agent_id).execution_options(autoflush=False)
    )
    if not _assistant_thread_of(thread_id, agent_id) or tenant_id != ctx.tenant_id:
        raise agent_not_found()
    return saver()
