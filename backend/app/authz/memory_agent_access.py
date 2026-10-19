"""Which agent a caller's memory may reference, and who edits an agent's memory (11.8; F-09).

* User x agent memory (``memory_records.scope='agent'``) always belongs to the caller
  (``user_id = me``). On my own agent nothing changes. Once ``agent`` is enforced, a
  caller holding ``agent#can_run`` on someone else's agent of this organization keeps
  their own memory for it; the owner never sees it (the record predicate stays private).
* Agent memory settings and the shared operating memo (``AGENTS.md``) belong to the
  agent: a ``can_edit`` holder works on them in the owner's scope.

``legacy``/``shadow`` answer with the existing owner-only rows and no extra query.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_access import is_owner, owner_context, require_agent
from app.authz.conversation_agent_checks import require_scoped_agent_permission
from app.authz.org_context import OrgContext
from app.authz.run_agent_loader import load_shared_run_agent, shared_runs_enforced
from app.authz.scoping import private_scope_predicate
from app.authz.service import AuthzService
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.memory import MemoryProposal, MemoryRecord

_DENIED = frozenset({403, 404})


async def owned_agent(db: AsyncSession, agent_id: UUID, context: OrgContext) -> Agent | None:
    return (
        await db.execute(
            select(Agent).where(Agent.id == agent_id, private_scope_predicate(Agent, context))
        )
    ).scalar_one_or_none()


async def _shared_agent(db: AsyncSession, agent_id: UUID, context: OrgContext) -> Agent | None:
    try:
        return await load_shared_run_agent(db, agent_id, context, [])
    except AppError as denied:  # hidden or not runnable: the memory path's own "not found"
        if denied.status in _DENIED:
            return None
        raise  # OpenFGA down stays 503


async def memory_agent(db: AsyncSession, agent_id: UUID, context: OrgContext) -> Agent | None:
    """My agent, else (``agent`` enforced) someone else's agent I may run, else ``None``."""
    agent = await owned_agent(db, agent_id, context)
    if agent is not None:
        return agent
    return await _shared_agent(db, agent_id, context)


def shared_ids(agent: Agent | None, context: OrgContext) -> frozenset[UUID]:
    """The non-owned agent ids the memory predicates admit for this caller."""
    if agent is None or agent.user_id == context.user_id:
        return frozenset()
    return frozenset({agent.id})


async def shared_memory_agent_ids(
    db: AsyncSession, agent_id: UUID | None, context: OrgContext
) -> frozenset[UUID]:
    """``shared_ids`` without a lookup while shared runs are not enforced (legacy SQL)."""
    if agent_id is None or not shared_runs_enforced():
        return frozenset()
    if await owned_agent(db, agent_id, context) is not None:
        return frozenset()
    return shared_ids(await _shared_agent(db, agent_id, context), context)


async def row_shared_memory_agent_ids(
    db: AsyncSession,
    model: type[MemoryRecord] | type[MemoryProposal],
    row_id: UUID,
    context: OrgContext,
) -> frozenset[UUID]:
    """``shared_memory_agent_ids`` for my record/proposal ``row_id`` (by its agent)."""
    if not shared_runs_enforced():
        return frozenset()
    agent_id = await db.scalar(
        select(model.agent_id)
        .where(model.id == row_id, private_scope_predicate(model, context))
        .execution_options(autoflush=False)
    )
    return await shared_memory_agent_ids(db, agent_id, context)


async def pending_memo_proposals(db: AsyncSession, context: OrgContext, agent_id: UUID) -> int:
    """My pending ``agent_memo`` proposals on one agent (the per-user cap, 11.8)."""
    count = await db.scalar(
        select(func.count())
        .select_from(MemoryProposal)
        .where(
            MemoryProposal.agent_id == agent_id,
            MemoryProposal.user_id == context.user_id,
            MemoryProposal.scope == "agent_memo",
            MemoryProposal.status == "pending",
        )
    )
    return int(count or 0)


async def agent_edit_context(
    db: AsyncSession, authz: AuthzService, context: OrgContext, agent_id: UUID
) -> OrgContext:
    """Decide ``agent#can_edit``, then the scope agent-owned memory state is used in.

    An allowed non-owner editor works in the owner's scope (like the Builder Assistant,
    11.7). Outside enforce: ``context`` (legacy: no extra query).
    """
    await require_scoped_agent_permission(db, context, agent_id, "can_edit", authz=authz)
    if not authz.enforces("agent"):
        return context
    agent = await db.scalar(
        select(Agent)
        .where(
            Agent.id == agent_id,
            Agent.org_id == context.org_id,
            Agent.tenant_id == context.tenant_id,
        )
        .execution_options(autoflush=False)
    )
    if agent is None or is_owner(agent, context):
        return context
    return owner_context(agent)


async def memo_agent(
    db: AsyncSession, authz: AuthzService, context: OrgContext, agent_id: UUID, permission: str
) -> Agent:
    """``RES(agent, permission)`` for the memo routes; legacy answer = my own agent."""
    agent = await require_agent(
        db,
        authz,
        context,
        agent_id,
        permission,
        legacy=await owned_agent(db, agent_id, context),
        not_found=agent_not_found(),
    )
    if not is_owner(agent, context) and agent.runtime_profile != AGENT_RUNTIME_PROFILE_STANDARD:
        raise agent_not_found()  # hidden (builder) agents are never shared
    return agent
