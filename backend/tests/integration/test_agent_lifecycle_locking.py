from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.tool import Tool
from app.services.agent_lifecycle_locks import (
    AgentTreeSnapshot,
    acquire_agent_lifecycle_locks,
    lock_agent_tree,
)
from tests.authz.agent_lifecycle_fixture import LifecycleSentinel, registered_lifecycle_fixture
from tests.authz.agent_lifecycle_revoked_fixture import run_revoked_owner_maintenance_history
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_conversation_run_delete_races import (
    SeededGraph,
    _backend_pid,
    _wait_for_lock,
)
from tests.integration.test_conversation_run_delete_races import graph as graph
from tests.integration.test_conversation_run_delete_races import pg_factory as pg_factory


async def test_archive_lock_wins_before_acceptance(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: archive holds the lifecycle lock before execution acceptance.
    sentinel = LifecycleSentinel()
    async with pg_factory() as archive_db, pg_factory() as acceptance_db:
        await lock_agent_tree(archive_db, graph.agent_id)
        agent = await archive_db.get(Agent, graph.agent_id)
        assert agent is not None
        agent.archived_at = datetime.now(UTC)
        await archive_db.flush()
        acceptance_pid = await _backend_pid(acceptance_db)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def accept() -> None:
            attempted.set()
            with pytest.raises(AppError) as raised:
                await load_agent_tree_for_execution(
                    acceptance_db,
                    AgentExecutionTarget(graph.agent_id, unit_context(graph.user_id)),
                )
            assert raised.value.code == "AGENT_ARCHIVED"
            await acceptance_db.rollback()
            finished.set()

        # When: acceptance overlaps archive commit.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(accept)
            await attempted.wait()
            await _wait_for_lock(archive_db, acceptance_pid)
            assert not finished.is_set()
            await archive_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    # Then: execution never registers ownership or invokes a provider.
    assert sentinel.observed_stage_counts == (0, 0, 0, 0, 0)
    assert sentinel.provider_invocations == 0


async def test_acceptance_registers_before_archive(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: acceptance holds the lifecycle lock and stages durable ownership.
    sentinel = LifecycleSentinel()
    async with pg_factory() as acceptance_db, pg_factory() as archive_db:
        await load_agent_tree_for_execution(
            acceptance_db,
            AgentExecutionTarget(graph.agent_id, unit_context(graph.user_id)),
        )
        run = ConversationRun(
            conversation_id=graph.conversation_id,
            agent_id=graph.agent_id,
            user_id=graph.user_id,
            source="chat",
            status="queued",
            is_active=True,
        )
        acceptance_db.add(run)
        await acceptance_db.flush()
        sentinel.observe("registered")
        archive_pid = await _backend_pid(archive_db)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def archive() -> None:
            attempted.set()
            await lock_agent_tree(archive_db, graph.agent_id)
            agent = await archive_db.get(Agent, graph.agent_id)
            assert agent is not None
            agent.archived_at = datetime.now(UTC)
            await archive_db.commit()
            finished.set()

        # When: archive overlaps the uncommitted accepted Run.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(archive)
            await attempted.wait()
            await _wait_for_lock(acceptance_db, archive_pid)
            assert not finished.is_set()
            await acceptance_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    # Then: durable registration won, while execution/provider start did not occur.
    async with pg_factory() as verify_db:
        persisted = await verify_db.get(ConversationRun, run.id)
        assert persisted is not None
        assert persisted.status == "queued"
    assert sentinel.observed_stage_counts == (0, 1, 0, 0, 0)
    assert sentinel.provider_invocations == 0


async def test_dirty_execution_lock_blocks_archive_without_flushing_pending_write(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    """An unrelated pending row stays private while its transaction owns the tree lock."""
    context = unit_context(graph.user_id)
    pending = Tool(
        id=uuid4(),
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Pending native lifecycle proof",
        definition_key="http_request",
    )
    async with pg_factory() as acceptance_db, pg_factory() as archive_db:
        acceptance_db.add(pending)
        transaction = acceptance_db.get_transaction()
        flush = Mock()
        event.listen(acceptance_db.sync_session, "before_flush", flush)
        try:
            await load_agent_tree_for_execution(
                acceptance_db,
                AgentExecutionTarget(graph.agent_id, context),
            )
        finally:
            event.remove(acceptance_db.sync_session, "before_flush", flush)
        assert pending in acceptance_db.new
        assert acceptance_db.get_transaction() is transaction
        assert flush.call_count == 0

        archive_pid = await _backend_pid(archive_db)
        finished = anyio.Event()

        async def archive() -> None:
            await lock_agent_tree(archive_db, graph.agent_id)
            agent = await archive_db.get(Agent, graph.agent_id)
            assert agent is not None
            agent.archived_at = datetime.now(UTC)
            await archive_db.commit()
            finished.set()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(archive)
            await _wait_for_lock(acceptance_db, archive_pid)
            assert not finished.is_set()
            async with pg_factory() as observer_db:
                assert (
                    await observer_db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None
                )
            assert pending in acceptance_db.new
            assert flush.call_count == 0
            await acceptance_db.rollback()
            with anyio.fail_after(5):
                await finished.wait()

    async with pg_factory() as verify_db:
        assert await verify_db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None


async def test_graph_retry_returns_complete_tree_after_delegation_change(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: a blocker pauses acceptance after its pre-lock graph read.
    async with pg_factory() as seed_db:
        root = await seed_db.get(Agent, graph.agent_id)
        assert root is not None
        child = Agent(
            user_id=root.user_id,
            org_id=root.org_id,
            tenant_id=root.tenant_id,
            name="Graph retry child",
            system_prompt="fixture",
            model_id=root.model_id,
        )
        seed_db.add(child)
        await seed_db.commit()
        child_id = child.id

    async with (
        pg_factory() as blocker_db,
        pg_factory() as acceptance_db,
        pg_factory() as mutation_db,
    ):
        await acquire_agent_lifecycle_locks(blocker_db, (graph.agent_id,))
        acceptance_pid = await _backend_pid(acceptance_db)
        result: AgentTreeSnapshot | None = None
        finished = anyio.Event()

        async def accept() -> None:
            nonlocal result
            result = await lock_agent_tree(acceptance_db, graph.agent_id)
            finished.set()

        # When: delegation changes while acceptance waits on the advisory lock.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(accept)
            await _wait_for_lock(blocker_db, acceptance_pid)
            mutation_db.add(
                AgentSubAgentLink(parent_agent_id=graph.agent_id, sub_agent_id=child_id)
            )
            await mutation_db.commit()
            await blocker_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

        # Then: the full transaction retried and accepted the complete new tree.
        assert result is not None
        assert result.agent_ids == tuple(
            sorted((graph.agent_id, child_id), key=lambda item: item.bytes)
        )
        await acceptance_db.rollback()

    async with pg_factory() as cleanup_db:
        await cleanup_db.execute(
            delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == child_id)
        )
        await cleanup_db.execute(delete(Agent).where(Agent.id == child_id))
        await cleanup_db.commit()


async def test_transaction_lock_releases_on_commit(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: one transaction owns the Agent lifecycle lock.
    async with pg_factory() as first_db, pg_factory() as second_db:
        await lock_agent_tree(first_db, graph.agent_id)
        second_pid = await _backend_pid(second_db)
        finished = anyio.Event()

        async def acquire() -> None:
            await lock_agent_tree(second_db, graph.agent_id)
            finished.set()

        # When: the first transaction commits.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(acquire)
            await _wait_for_lock(first_db, second_pid)
            assert not finished.is_set()
            await first_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

        # Then: the transaction-scoped lock is released for the waiter.
        assert finished.is_set()
        await second_db.rollback()


async def test_revoked_owner_maintenance_preserves_history_and_workerless_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        expected_attachment = fixture.graph.attachment_path.read_bytes()
        expected_artifact = fixture.graph.artifact_version_paths[-1].read_bytes()
        proof = await run_revoked_owner_maintenance_history(fixture)

    assert proof.revoked_ack and proof.inactive_ack
    assert proof.remote_ack_absent
    assert proof.input_revision == 2
    assert proof.terminal_events == proof.audit_events == 1
    assert proof.retained_attachment == expected_attachment
    assert proof.retained_artifact == expected_artifact
    assert proof.retained_history_masked
    assert proof.revoked_denial == (403, "forbidden")
