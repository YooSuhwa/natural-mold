"""Execution-only Agent tree scope separated from retained history reads."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import agent_archived
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.services.agent_lifecycle_locks import (
    AgentTreeSnapshot,
    lock_agent_tree_preserving_unrelated_writes,
    read_agent_tree,
)


@dataclass(frozen=True, slots=True)
class AgentExecutionTarget:
    root_agent_id: UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class ExecutableAgentTree:
    snapshot: AgentTreeSnapshot
    agents: tuple[Agent, ...]


def assert_agent_tree_executable(snapshot: AgentTreeSnapshot, context: OrgContext) -> None:
    """Reject missing, stale-scope, or archived metadata before private hydration."""
    if not snapshot.members or snapshot.root_agent_id not in snapshot.agent_ids:
        raise agent_not_found()
    for member in snapshot.members:
        if (
            member.user_id != context.user_id
            or member.org_id != context.org_id
            or member.tenant_id != context.tenant_id
        ):
            raise agent_not_found()
    if any(member.archived for member in snapshot.members):
        raise agent_archived()


def assert_agent_root_mutable(snapshot: AgentTreeSnapshot, context: OrgContext) -> None:
    """Authorize only the root while retaining full-tree lifecycle serialization."""
    root = next(
        (member for member in snapshot.members if member.agent_id == snapshot.root_agent_id),
        None,
    )
    if (
        root is None
        or root.user_id != context.user_id
        or root.org_id != context.org_id
        or root.tenant_id != context.tenant_id
    ):
        raise agent_not_found()
    if root.archived:
        raise agent_archived()


async def authorize_agent_root_mutation(db: AsyncSession, target: AgentExecutionTarget) -> None:
    """Lock the current tree and authorize root cleanup without child content reads."""
    await require_private_actor(db, target.context)
    snapshot = await lock_agent_tree_preserving_unrelated_writes(db, target.root_agent_id)
    assert_agent_root_mutable(snapshot, target.context)


async def load_agent_tree_for_execution(
    db: AsyncSession, target: AgentExecutionTarget
) -> ExecutableAgentTree:
    """Fresh-authorize and lock a complete active tree before loading Agent content."""
    await require_private_actor(db, target.context)
    with db.no_autoflush:
        snapshot = await lock_agent_tree_preserving_unrelated_writes(db, target.root_agent_id)
        assert_agent_tree_executable(snapshot, target.context)
        agents = tuple(
            sorted(
                await db.scalars(
                    select(Agent)
                    .where(
                        Agent.id.in_(snapshot.agent_ids),
                        private_scope_predicate(Agent, target.context),
                    )
                    .execution_options(populate_existing=True, autoflush=False)
                ),
                key=lambda agent: agent.id.bytes,
            )
        )
    if tuple(agent.id for agent in agents) != snapshot.agent_ids:
        raise agent_not_found()
    return ExecutableAgentTree(snapshot=snapshot, agents=agents)


async def load_agent_tree_for_history(db: AsyncSession, root_agent_id: UUID) -> AgentTreeSnapshot:
    """Load retained metadata without applying the execution archive fence."""
    return await read_agent_tree(db, root_agent_id)


async def load_agent_tree_for_maintenance(
    db: AsyncSession, root_agent_id: UUID
) -> AgentTreeSnapshot:
    """Load lifecycle metadata for cancellation/finalization after archive."""
    return await read_agent_tree(db, root_agent_id)
