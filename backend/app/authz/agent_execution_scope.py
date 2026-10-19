"""Execution-only Agent tree scope separated from retained history reads."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import agent_archived
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.execution_transaction_tracker import claimed_locks
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import execution_scope, live_proof
from app.authz.private_actor import require_private_actor
from app.authz.run_agent_loader import authorize_shared_tree, run_agent_predicate
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
    # A run path (chat, queue, subagents): someone else's agent tree of this organization
    # loads under the caller's ``agent#can_run`` once ``agent`` is enforced (F-06).
    run: bool = False


@dataclass(frozen=True, slots=True)
class ExecutableAgentTree:
    snapshot: AgentTreeSnapshot
    agents: tuple[Agent, ...]


def assert_agent_tree_executable(
    snapshot: AgentTreeSnapshot, context: OrgContext, *, shared: bool = False
) -> None:
    """Reject missing, stale-scope, or archived metadata before private hydration.

    ``shared`` (``authorize_shared_tree`` admitted it): members only share the org."""
    if not snapshot.members or snapshot.root_agent_id not in snapshot.agent_ids:
        raise agent_not_found()
    for member in snapshot.members:
        if (
            (member.user_id != context.user_id and not shared)
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
    """Fresh-authorize and lock a complete active tree before loading Agent content.

    Inside a live execution proof the tree's lifecycle locks are already held,
    so the exact tree is re-read without lock SQL; any other tree is denied.
    """
    proof = live_proof(db)
    if proof is None:
        # Canonical order: the parent/member/User prefix precedes any
        # lifecycle advisory lock even when no execution proof is issued.
        from app.authz.organization_execution_gateway import require_execution_prefix

        await require_private_actor(db, target.context)
        await require_execution_prefix(db, target.context)
    with db.no_autoflush:
        if proof is not None:
            proof.require_scope(execution_scope(target.context))
            snapshot = await read_agent_tree(db, target.root_agent_id)
            if target.root_agent_id not in proof.agent_ids or not set(snapshot.agent_ids).issubset(
                proof.agent_ids
            ):
                raise ExecutionProofError("execution_scope_expansion_denied")
        else:
            with claimed_locks(db):
                snapshot = await lock_agent_tree_preserving_unrelated_writes(
                    db, target.root_agent_id
                )
        shared = target.run and await authorize_shared_tree(snapshot, target.context)
        assert_agent_tree_executable(snapshot, target.context, shared=shared)
        agents = tuple(
            sorted(
                await db.scalars(
                    select(Agent)
                    .where(
                        Agent.id.in_(snapshot.agent_ids),
                        run_agent_predicate(target.context, shared=shared),
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
