"""Transaction-scoped serialization for Agent lifecycle writers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import blake2b
from typing import Final
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink

_ADVISORY_NAMESPACE: Final = b"moldy-agent-v1"
_GRAPH_RETRY_LIMIT: Final = 3


@dataclass(frozen=True, slots=True)
class AgentLifecycleLockKey:
    agent_id: UUID
    key1: int
    key2: int


@dataclass(frozen=True, slots=True)
class AgentTreeMember:
    agent_id: UUID
    user_id: UUID
    org_id: UUID
    tenant_id: UUID
    archived: bool


@dataclass(frozen=True, slots=True)
class AgentTreeEdge:
    parent_agent_id: UUID
    child_agent_id: UUID


@dataclass(frozen=True, slots=True)
class AgentTreeSnapshot:
    root_agent_id: UUID
    members: tuple[AgentTreeMember, ...]
    edges: tuple[AgentTreeEdge, ...]

    @property
    def agent_ids(self) -> tuple[UUID, ...]:
        return tuple(member.agent_id for member in self.members)


class AgentLifecycleTransactionDirtyError(RuntimeError):
    __slots__ = ("root_agent_id",)

    def __init__(self, root_agent_id: UUID) -> None:
        self.root_agent_id = root_agent_id
        super().__init__(root_agent_id)

    def __str__(self) -> str:
        return f"Agent lifecycle locking requires a clean session: {self.root_agent_id}"


class AgentTreeChangedError(RuntimeError):
    __slots__ = ("root_agent_id", "attempts")

    def __init__(self, root_agent_id: UUID, attempts: int) -> None:
        self.root_agent_id = root_agent_id
        self.attempts = attempts
        super().__init__(root_agent_id, attempts)

    def __str__(self) -> str:
        return (
            f"Agent tree changed during {self.attempts} lifecycle lock attempts: "
            f"{self.root_agent_id}"
        )


def lifecycle_advisory_key(agent_id: UUID) -> AgentLifecycleLockKey:
    """Map an Agent UUID to PostgreSQL's stable signed two-int32 lock form."""
    digest = blake2b(agent_id.bytes, digest_size=8, person=_ADVISORY_NAMESPACE).digest()
    return AgentLifecycleLockKey(
        agent_id=agent_id,
        key1=int.from_bytes(digest[:4], byteorder="big", signed=True),
        key2=int.from_bytes(digest[4:], byteorder="big", signed=True),
    )


def ordered_lifecycle_lock_keys(agent_ids: Iterable[UUID]) -> tuple[AgentLifecycleLockKey, ...]:
    """Deduplicate and order lifecycle locks by UUID bytes before acquisition."""
    return tuple(
        lifecycle_advisory_key(agent_id)
        for agent_id in sorted(set(agent_ids), key=lambda item: item.bytes)
    )


async def read_agent_tree(db: AsyncSession, root_agent_id: UUID) -> AgentTreeSnapshot:
    """Read only identity/scope/archive metadata for one executable tree."""
    tree = select(Agent.id.label("agent_id")).where(Agent.id == root_agent_id).cte(recursive=True)
    tree = tree.union(
        select(AgentSubAgentLink.sub_agent_id).join(
            tree, AgentSubAgentLink.parent_agent_id == tree.c.agent_id
        )
    )
    agent_ids = tuple(
        sorted(
            set(await db.scalars(select(tree.c.agent_id).execution_options(autoflush=False))),
            key=lambda item: item.bytes,
        )
    )
    if not agent_ids:
        return AgentTreeSnapshot(root_agent_id=root_agent_id, members=(), edges=())
    rows = (
        await db.execute(
            select(
                Agent.id,
                Agent.user_id,
                Agent.org_id,
                Agent.tenant_id,
                Agent.archived_at,
            )
            .where(Agent.id.in_(agent_ids))
            .execution_options(populate_existing=True, autoflush=False)
        )
    ).all()
    links = (
        await db.execute(
            select(AgentSubAgentLink.parent_agent_id, AgentSubAgentLink.sub_agent_id)
            .where(
                AgentSubAgentLink.parent_agent_id.in_(agent_ids),
                AgentSubAgentLink.sub_agent_id.in_(agent_ids),
            )
            .execution_options(autoflush=False)
        )
    ).all()
    members = tuple(
        sorted(
            (
                AgentTreeMember(
                    agent_id=row.id,
                    user_id=row.user_id,
                    org_id=row.org_id,
                    tenant_id=row.tenant_id,
                    archived=row.archived_at is not None,
                )
                for row in rows
            ),
            key=lambda member: member.agent_id.bytes,
        )
    )
    edges = tuple(
        sorted(
            (
                AgentTreeEdge(
                    parent_agent_id=row.parent_agent_id,
                    child_agent_id=row.sub_agent_id,
                )
                for row in links
            ),
            key=lambda edge: (edge.parent_agent_id.bytes, edge.child_agent_id.bytes),
        )
    )
    return AgentTreeSnapshot(root_agent_id=root_agent_id, members=members, edges=edges)


async def acquire_agent_lifecycle_locks(db: AsyncSession, agent_ids: Iterable[UUID]) -> None:
    """Acquire transaction locks before any Conversation/Run/Input row lock."""
    for lock in ordered_lifecycle_lock_keys(agent_ids):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key1, :key2)"),
            {"key1": lock.key1, "key2": lock.key2},
        )


async def lock_agent_tree(db: AsyncSession, root_agent_id: UUID) -> AgentTreeSnapshot:
    """Lock a stable tree at a clean, metadata-only transaction boundary.

    A graph drift rolls back the whole metadata transaction so transaction-level
    locks release before the retry. Callers must invoke this before staging any
    writes; pending ORM state is rejected rather than silently discarded.
    """
    if db.new or db.dirty or db.deleted:
        raise AgentLifecycleTransactionDirtyError(root_agent_id)
    for _attempt in range(_GRAPH_RETRY_LIMIT):
        before = await read_agent_tree(db, root_agent_id)
        await acquire_agent_lifecycle_locks(db, before.agent_ids or (root_agent_id,))
        after = await read_agent_tree(db, root_agent_id)
        if after == before:
            return after
        await db.rollback()
    raise AgentTreeChangedError(root_agent_id=root_agent_id, attempts=_GRAPH_RETRY_LIMIT)


def _pending_tree_state_intersects(db: AsyncSession, snapshot: AgentTreeSnapshot) -> bool:
    """Detect staged ORM state that could change or be clobbered by this tree load."""
    agent_ids = set(snapshot.agent_ids)
    agent_ids.add(snapshot.root_agent_id)
    for entity in (*db.new, *db.dirty, *db.deleted):
        entity_type = type(entity)
        if entity_type is Agent and entity.id in agent_ids:
            return True
        if entity_type is AgentSubAgentLink:
            parent_id = entity.parent_agent_id
            if parent_id is None or parent_id in agent_ids:
                return True
    return False


async def lock_agent_tree_preserving_unrelated_writes(
    db: AsyncSession, root_agent_id: UUID
) -> AgentTreeSnapshot:
    """Lock an execution tree without flushing unrelated caller-owned ORM state.

    Clean callers retain the retrying transaction boundary. Dirty callers may
    proceed only when their staged state cannot affect this Agent tree. A graph
    drift aborts without rollback so the caller remains responsible for its
    pending state and no protected mutation can follow a stale snapshot.
    """
    if not (db.new or db.dirty or db.deleted):
        return await lock_agent_tree(db, root_agent_id)
    with db.no_autoflush:
        before = await read_agent_tree(db, root_agent_id)
        if _pending_tree_state_intersects(db, before):
            raise AgentLifecycleTransactionDirtyError(root_agent_id)
        await acquire_agent_lifecycle_locks(db, before.agent_ids or (root_agent_id,))
        after = await read_agent_tree(db, root_agent_id)
    if after != before:
        raise AgentTreeChangedError(root_agent_id=root_agent_id, attempts=1)
    return after
