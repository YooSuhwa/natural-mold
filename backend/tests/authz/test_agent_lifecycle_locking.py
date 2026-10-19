from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    assert_agent_tree_executable,
    load_agent_tree_for_execution,
    load_agent_tree_for_history,
    load_agent_tree_for_maintenance,
)
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.tool import Tool
from app.services import agent_lifecycle_locks
from app.services.agent_lifecycle_locks import (
    AgentLifecycleTransactionDirtyError,
    AgentTreeChangedError,
    AgentTreeEdge,
    AgentTreeMember,
    AgentTreeSnapshot,
    lifecycle_advisory_key,
    lock_agent_tree,
    lock_agent_tree_preserving_unrelated_writes,
    ordered_lifecycle_lock_keys,
    read_agent_tree,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, make_agent, seed_agent


async def _seed_tree(db: AsyncSession, *, archived_child: bool) -> tuple[UUID, UUID]:
    _, model, root = await seed_agent(db, name="Lifecycle root")
    child = await make_agent(
        db,
        user_id=TEST_USER_ID,
        model_id=model.id,
        name="Lifecycle child",
        archived_at=datetime.now(UTC) if archived_child else None,
    )
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id))
    await db.commit()
    return root.id, child.id


def test_lifecycle_key_is_stable_signed_int32_pair() -> None:
    # Given: one fixed UUID whose key must be process-independent.
    agent_id = UUID("12345678-1234-5678-9abc-def012345678")

    # When: the advisory key is derived twice.
    first = lifecycle_advisory_key(agent_id)
    second = lifecycle_advisory_key(agent_id)

    # Then: the exact namespace result is stable and PostgreSQL int32-safe.
    assert first == second
    assert (first.key1, first.key2) == (-1469899359, -633788924)
    assert -(2**31) <= first.key1 < 2**31
    assert -(2**31) <= first.key2 < 2**31


def test_lifecycle_keys_are_unique_and_uuid_byte_sorted() -> None:
    # Given: deterministic UUIDs in reverse order with a duplicate.
    sampled = tuple(uuid5(NAMESPACE_URL, f"moldy/lifecycle/{index}") for index in range(4096))
    requested = (*reversed(sampled), sampled[0])

    # When: keys are prepared for multi-Agent acquisition.
    ordered = ordered_lifecycle_lock_keys(requested)

    # Then: one unique key per UUID is acquired in UUID-byte order.
    expected = tuple(sorted(sampled, key=lambda item: item.bytes))
    assert tuple(item.agent_id for item in ordered) == expected
    assert len({(item.key1, item.key2) for item in ordered}) == len(sampled)


async def test_history_loader_keeps_archived_tree_available(db: AsyncSession) -> None:
    # Given: an archived child remains part of retained history.
    root_id, child_id = await _seed_tree(db, archived_child=True)

    # When: the history-safe loader reads the tree.
    snapshot = await load_agent_tree_for_history(db, root_id)

    # Then: it returns the archived participant without an execution fence.
    assert snapshot.agent_ids == tuple(sorted((root_id, child_id), key=lambda item: item.bytes))
    assert any(member.archived for member in snapshot.members)


async def test_maintenance_loader_keeps_archived_tree_available(db: AsyncSession) -> None:
    # Given: maintenance must cancel work after archive.
    root_id, child_id = await _seed_tree(db, archived_child=True)

    # When: maintenance loads the lifecycle metadata.
    snapshot = await load_agent_tree_for_maintenance(db, root_id)

    # Then: the archived tree is still reachable.
    assert snapshot.agent_ids == tuple(sorted((root_id, child_id), key=lambda item: item.bytes))


async def test_history_tree_is_execution_denied_when_archived(db: AsyncSession) -> None:
    # Given: the current owner has an archived executable child.
    root_id, _ = await _seed_tree(db, archived_child=True)

    snapshot = await load_agent_tree_for_history(db, root_id)

    # When: the execution-only assertion checks the metadata.
    with pytest.raises(AppError) as raised:
        assert_agent_tree_executable(snapshot, unit_context(TEST_USER_ID))

    # Then: the fixed lifecycle error denies execution.
    assert raised.value.code == "AGENT_ARCHIVED"
    assert raised.value.status == 409


async def test_active_tree_passes_execution_assertion(db: AsyncSession) -> None:
    # Given: an active root and child both belong to the current private actor.
    root_id, child_id = await _seed_tree(db, archived_child=False)

    snapshot = await load_agent_tree_for_history(db, root_id)

    # When: the execution-only assertion checks the metadata.
    assert_agent_tree_executable(snapshot, unit_context(TEST_USER_ID))

    # Then: the complete active tree remains accepted.
    expected = tuple(sorted((root_id, child_id), key=lambda item: item.bytes))
    assert snapshot.agent_ids == expected


async def test_read_tree_preserves_complete_parent_child_graph(db: AsyncSession) -> None:
    # Given: an active two-node delegation graph.
    root_id, child_id = await _seed_tree(db, archived_child=False)

    # When: the metadata-only graph is read.
    snapshot = await read_agent_tree(db, root_id)

    # Then: node and edge identity is exact.
    assert snapshot.agent_ids == tuple(sorted((root_id, child_id), key=lambda item: item.bytes))
    assert [(edge.parent_agent_id, edge.child_agent_id) for edge in snapshot.edges] == [
        (root_id, child_id)
    ]


async def test_lifecycle_lock_rejects_staged_orm_writes(db: AsyncSession) -> None:
    # Given: a caller has already staged a mutation before lifecycle locking.
    root_id, _ = await _seed_tree(db, archived_child=False)
    root = await db.get(Agent, root_id)
    assert root is not None
    root.name = "Uncommitted mutation"

    # When/Then: locking refuses to risk discarding the caller's pending write.
    with pytest.raises(AgentLifecycleTransactionDirtyError):
        await lock_agent_tree(db, root_id)


def test_lifecycle_dirty_error_propagates_through_contextmanager() -> None:
    """RuntimeError propagation must retain its exact typed exception and traceback."""

    @contextmanager
    def passthrough() -> Iterator[None]:
        yield

    error = AgentLifecycleTransactionDirtyError(UUID("12345678-1234-5678-9abc-def012345678"))
    with pytest.raises(AgentLifecycleTransactionDirtyError) as raised, passthrough():
        raise error
    assert raised.value is error
    assert raised.value.__traceback__ is not None


async def test_execution_lock_preserves_unrelated_pending_write_without_flush(
    db: AsyncSession,
) -> None:
    root_id, _ = await _seed_tree(db, archived_child=False)
    context = unit_context(TEST_USER_ID)
    pending = Tool(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Pending unrelated lifecycle proof",
        definition_key="http_request",
    )
    db.add(pending)
    transaction = db.get_transaction()
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        executable = await load_agent_tree_for_execution(db, AgentExecutionTarget(root_id, context))
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    assert executable.snapshot.root_agent_id == root_id
    assert pending in db.new
    assert db.get_transaction() is transaction
    assert flush.call_count == 0


async def test_execution_lock_rejects_pending_link_without_flush(db: AsyncSession) -> None:
    root_id, _ = await _seed_tree(db, archived_child=False)
    root = await db.get(Agent, root_id)
    assert root is not None
    unrelated = await make_agent(
        db,
        user_id=TEST_USER_ID,
        model_id=root.model_id,
        name="Pending link child",
    )
    await db.commit()
    pending = AgentSubAgentLink(parent_agent_id=root_id, sub_agent_id=unrelated.id)
    db.add(pending)
    transaction = db.get_transaction()
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(AgentLifecycleTransactionDirtyError):
            await load_agent_tree_for_execution(
                db, AgentExecutionTarget(root_id, unit_context(TEST_USER_ID))
            )
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    assert pending in db.new
    assert db.get_transaction() is transaction
    assert flush.call_count == 0


async def test_dirty_graph_drift_aborts_without_rollback(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_id = uuid5(NAMESPACE_URL, "moldy/lifecycle/dirty-drift-root")
    child_id = uuid5(NAMESPACE_URL, "moldy/lifecycle/dirty-drift-child")
    context = unit_context(TEST_USER_ID)
    root = AgentTreeMember(root_id, context.user_id, context.org_id, context.tenant_id, False)
    child = AgentTreeMember(child_id, context.user_id, context.org_id, context.tenant_id, False)
    before = AgentTreeSnapshot(root_id, (root,), ())
    after = AgentTreeSnapshot(root_id, (root, child), (AgentTreeEdge(root_id, child_id),))
    reads = iter((before, after))
    acquisitions: list[tuple[UUID, ...]] = []

    async def read_changed(_db: AsyncSession, _root_id: UUID) -> AgentTreeSnapshot:
        return next(reads)

    async def record_acquisition(_db: AsyncSession, agent_ids: tuple[UUID, ...]) -> None:
        acquisitions.append(agent_ids)

    monkeypatch.setattr(agent_lifecycle_locks, "read_agent_tree", read_changed)
    monkeypatch.setattr(
        agent_lifecycle_locks,
        "acquire_agent_lifecycle_locks",
        record_acquisition,
    )
    pending = Tool(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Pending graph drift proof",
        definition_key="http_request",
    )
    db.add(pending)
    transaction = db.get_transaction()
    with pytest.raises(AgentTreeChangedError) as raised:
        await lock_agent_tree_preserving_unrelated_writes(db, root_id)
    assert raised.value.attempts == 1
    assert acquisitions == [(root_id,)]
    assert pending in db.new
    assert db.get_transaction() is transaction


async def test_graph_retry_restarts_with_changed_complete_tree(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the graph expands between the pre-lock and post-lock reads.
    root_id = uuid5(NAMESPACE_URL, "moldy/lifecycle/retry-root")
    child_id = uuid5(NAMESPACE_URL, "moldy/lifecycle/retry-child")
    member = AgentTreeMember(
        agent_id=root_id,
        user_id=TEST_USER_ID,
        org_id=unit_context(TEST_USER_ID).org_id,
        tenant_id=unit_context(TEST_USER_ID).tenant_id,
        archived=False,
    )
    child = AgentTreeMember(
        agent_id=child_id,
        user_id=TEST_USER_ID,
        org_id=unit_context(TEST_USER_ID).org_id,
        tenant_id=unit_context(TEST_USER_ID).tenant_id,
        archived=False,
    )
    before = AgentTreeSnapshot(root_id, (member,), ())
    after = AgentTreeSnapshot(
        root_id,
        tuple(sorted((member, child), key=lambda item: item.agent_id.bytes)),
        (AgentTreeEdge(root_id, child_id),),
    )
    reads = iter((before, after, after, after))
    acquisitions: list[tuple[UUID, ...]] = []

    async def read_changed(_db: AsyncSession, _root_id: UUID) -> AgentTreeSnapshot:
        return next(reads)

    async def record_acquisition(_db: AsyncSession, agent_ids: tuple[UUID, ...]) -> None:
        acquisitions.append(agent_ids)

    monkeypatch.setattr(agent_lifecycle_locks, "read_agent_tree", read_changed)
    monkeypatch.setattr(
        agent_lifecycle_locks,
        "acquire_agent_lifecycle_locks",
        record_acquisition,
    )

    # When: lifecycle locking observes the changed post-lock snapshot.
    snapshot = await lock_agent_tree(db, root_id)

    # Then: it rolls back and locks the newly complete tree on a fresh transaction.
    assert snapshot == after
    retried_ids = tuple(sorted((root_id, child_id), key=lambda item: item.bytes))
    assert acquisitions == [(root_id,), retried_ids]
