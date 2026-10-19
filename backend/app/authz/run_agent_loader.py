"""Load someone else's agent for a run under the caller's ``agent#can_run`` (11.0, 11.1; F-06).

Every run path loads the agent tree in the caller's private scope first (owner fast path:
the existing query, nothing else in ``legacy``/``shadow``). Only when that finds nothing
and the ``agent`` type is enforced is the agent read in the current organization and the
caller's ``agent#can_run`` decided (404 when hidden, 403 permission_required when only
visible). Children owned by others are admitted at load time; ``RuntimeAccessPlanner``
decides each child link for the 11.1 subject before the run binds it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_agent_checks import current_authz, require_agent_permission
from app.authz.org_context import OrgContext
from app.authz.scoping import org_scoped, private_scope_predicate
from app.authz.service import Verdict
from app.error_codes import agent_not_found
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.conversation import Conversation

if TYPE_CHECKING:
    from app.services.agent_lifecycle_locks import AgentTreeSnapshot


def shared_runs_enforced() -> bool:
    return current_authz().enforces("agent")


async def authorize_shared_tree(snapshot: AgentTreeSnapshot, context: OrgContext) -> bool:
    """``True`` when a run may load this tree beyond the caller's own agents.

    Owned trees (and every tree while ``agent`` is not enforced) answer ``False`` without
    a decision, so the existing owner assertion stays the gate.
    """
    members = snapshot.members
    if all(member.user_id == context.user_id for member in members):
        return False
    if not shared_runs_enforced():
        return False
    if any((m.org_id, m.tenant_id) != (context.org_id, context.tenant_id) for m in members):
        raise agent_not_found()
    root = next((m for m in members if m.agent_id == snapshot.root_agent_id), None)
    if root is not None and root.user_id != context.user_id:
        await require_agent_permission(context, root.agent_id, "can_run", legacy=Verdict.NOT_FOUND)
    return True


def run_agent_predicate(context: OrgContext, *, shared: bool) -> ColumnElement[bool]:
    if shared:  # someone else's: only standard agents are ever shared for runs
        return or_(
            private_scope_predicate(Agent, context),
            and_(
                Agent.org_id == context.org_id,
                Agent.tenant_id == context.tenant_id,
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            ),
        )
    return private_scope_predicate(Agent, context)


def run_agent_scope_predicate(context: OrgContext) -> ColumnElement[bool]:
    """The run's agent row: mine, or (``agent`` enforced) one of this organization — for my
    conversation's agent under a locked proof; the run itself decides ``can_run``."""
    return run_agent_predicate(context, shared=shared_runs_enforced())


async def require_shared_run_agent(agent: Agent, context: OrgContext) -> None:
    """The caller's ``agent#can_run`` on someone else's agent (memoized per run)."""
    if agent.user_id != context.user_id:
        await require_agent_permission(context, agent.id, "can_run", legacy=Verdict.NOT_FOUND)


async def load_shared_run_agent(
    db: AsyncSession, agent_id: UUID, context: OrgContext, options: list[Any]
) -> Agent | None:
    """Someone else's standard, active agent of this organization the caller may run.

    ``None`` while ``agent`` is not enforced (the caller keeps the existing 404).
    """
    if not shared_runs_enforced():
        return None
    agent = (
        await db.execute(
            org_scoped(select(Agent).where(Agent.id == agent_id), Agent, context)
            .where(
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                Agent.archived_at.is_(None),
            )
            .execution_options(populate_existing=True, autoflush=False)
            .options(*options)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise agent_not_found()
    await require_agent_permission(context, agent.id, "can_run", legacy=Verdict.NOT_FOUND)
    return agent


async def attach_shared_conversation_agent(
    db: AsyncSession,
    conversation: Conversation | None,
    context: OrgContext,
    options: Callable[[], list[Any]],
) -> None:
    """My conversation with someone else's agent: hydrate ``conversation.agent`` for the run
    when I may still run it (the private join left it empty)."""
    if conversation is None or conversation.agent is not None or conversation.agent_id is None:
        return
    agent = await load_shared_run_agent(db, conversation.agent_id, context, options())
    if agent is not None:
        set_committed_value(conversation, "agent", agent)


__all__ = [
    "attach_shared_conversation_agent",
    "authorize_shared_tree",
    "load_shared_run_agent",
    "run_agent_predicate",
    "run_agent_scope_predicate",
    "require_shared_run_agent",
    "shared_runs_enforced",
]
