from __future__ import annotations

import anyio
import pytest
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.agent_delegations import link_grants, lock_delegations, sync_agent_delegations
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.models.user import User
from app.services import agent_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.agent_lifecycle_locks import acquire_agent_lifecycle_locks
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.integration.test_conversation_run_delete_races import (
    SeededGraph,
    _assert_graph,
    _backend_pid,
    _create_run,
    _wait_for_lock,
)
from tests.integration.test_conversation_run_delete_races import (
    graph as graph,
)
from tests.integration.test_conversation_run_delete_races import (
    pg_factory as pg_factory,
)


async def test_agent_delete_rejects_run_while_worker_holds_run_lock(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: a durable active run and its worker's independent row lock.
    async with pg_factory() as seed_db:
        run = await _create_run(seed_db, graph)
        run_id = run.id
        seed_db.add(
            ConversationRunAgent(
                run_id=run.id,
                agent_id=graph.agent_id,
                agent_id_snapshot=graph.agent_id,
                role="root",
                path=[],
            )
        )
        await seed_db.commit()

    async with pg_factory() as worker_db, pg_factory() as delete_db:
        await worker_db.get(ConversationRun, run_id, with_for_update=True)
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        delete_pid = await _backend_pid(delete_db)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def archive() -> None:
            attempted.set()
            await agent_service.delete_agent(delete_db, agent, context=unit_context(graph.user_id))
            await delete_db.commit()
            finished.set()

        # When: archive waits for the owning worker's terminal row update.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(archive)
            await attempted.wait()
            await _wait_for_lock(worker_db, delete_pid)
            assert not finished.is_set()
            locked_run = await worker_db.get(ConversationRun, run_id)
            assert locked_run is not None
            locked_run.status = "completed"
            locked_run.is_active = False
            await worker_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)
    async with pg_factory() as verify_db:
        archived = await verify_db.get(Agent, graph.agent_id)
        assert archived is not None and archived.archived_at is not None


async def test_run_creation_first_fences_agent_delete_until_commit(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: run creation holds its conversation fence before committing.
    async with pg_factory() as create_db, pg_factory() as delete_db:
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        delete_pid = await _backend_pid(delete_db)
        run = await _create_run(create_db, graph)
        create_db.add(
            ConversationRunAgent(
                run_id=run.id,
                agent_id=graph.agent_id,
                agent_id_snapshot=graph.agent_id,
                role="root",
                path=[],
            )
        )
        attempted = anyio.Event()
        finished = anyio.Event()

        async def remove() -> None:
            attempted.set()
            await agent_service.delete_agent(delete_db, agent, context=unit_context(graph.user_id))
            await delete_db.commit()
            finished.set()

        # When: deletion contends with that same conversation's run creation.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(remove)
            await attempted.wait()
            await _wait_for_lock(create_db, delete_pid)
            assert not finished.is_set()
            await create_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    # Then: the committed run survives with durable cancellation intent.
    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)
    async with pg_factory() as verify_db:
        stored = await verify_db.get(ConversationRun, run.id)
        assert stored is not None
        assert stored.status == "canceling"
        assert stored.cancel_requested_at is not None


async def test_agent_archive_first_fences_owned_execution_then_returns_archived(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: archive owns the lifecycle lock before owned execution acceptance.
    async with pg_factory() as delete_db, pg_factory() as create_db:
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        create_pid = await _backend_pid(create_db)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def create() -> None:
            attempted.set()
            with pytest.raises(AppError) as raised:
                await accept_owned_execution(
                    create_db,
                    OwnedExecutionRequest(
                        conversation_id=graph.conversation_id,
                        root_agent_id=graph.agent_id,
                        context=unit_context(graph.user_id),
                        source="chat",
                        input_preview="archive race",
                    ),
                )
            assert raised.value.code == "AGENT_ARCHIVED"
            await create_db.rollback()
            finished.set()

        await agent_service.delete_agent(delete_db, agent, context=unit_context(graph.user_id))

        # When: the contender waits on the lifecycle lock until archive commits.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(create)
            await attempted.wait()
            await _wait_for_lock(delete_db, create_pid)
            assert not finished.is_set()
            await delete_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    # Then: retained history remains and no new run or participant was accepted.
    async with pg_factory() as verify_db:
        assert await verify_db.get(User, graph.user_id) is not None
        assert await verify_db.get(Conversation, graph.conversation_id) is not None
        run_count = await verify_db.scalar(
            select(func.count())
            .select_from(ConversationRun)
            .where(ConversationRun.conversation_id == graph.conversation_id)
        )
        participant_count = await verify_db.scalar(
            select(func.count())
            .select_from(ConversationRunAgent)
            .join(ConversationRun, ConversationRun.id == ConversationRunAgent.run_id)
            .where(ConversationRun.conversation_id == graph.conversation_id)
        )
        assert run_count == 0
        assert participant_count == 0
        archived = await verify_db.get(Agent, graph.agent_id)
        assert archived is not None and archived.archived_at is not None


async def test_deleting_an_already_archived_agent_with_conversations_succeeds(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Nightly E2E: the second DELETE of an archived agent reads archived_at back from
    # PostgreSQL (timezone-aware) and wrote it into the naive conversations.queue_paused_at;
    # asyncpg refused the bound value and the request failed with 500.
    for _ in range(2):
        async with pg_factory() as db:
            agent = await db.get(Agent, graph.agent_id)
            assert agent is not None
            await agent_service.delete_agent(db, agent, context=unit_context(graph.user_id))
            await db.commit()
    async with pg_factory() as verify_db:
        conversation = await verify_db.get(Conversation, graph.conversation_id)
        assert conversation is not None
        assert conversation.queue_paused is True
        assert conversation.queue_paused_at is not None


async def test_archive_that_revokes_a_subagent_delegation_does_not_deadlock_an_executor(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Nightly E2E deadlock: archiving a subagent revokes its parent's delegation grant,
    # which locks the Organization row FOR UPDATE. The archive took it after the agent
    # lifecycle lock, while queue recovery holds the Organization FOR SHARE (execution
    # prefix) and then waits for that lifecycle lock: PostgreSQL killed one of them.
    # Given: a parent that delegates to the graph's agent through a subagent link.
    context = unit_context(graph.user_id)
    async with pg_factory() as seed_db:
        child = await seed_db.get(Agent, graph.agent_id)
        assert child is not None
        parent = Agent(
            user_id=graph.user_id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name="Race Parent",
            system_prompt="x",
            model_id=child.model_id,
        )
        seed_db.add(parent)
        await seed_db.flush()
        await lock_delegations(seed_db, context)
        seed_db.add(
            AgentSubAgentLink(
                parent_agent_id=parent.id, sub_agent_id=child.id, delegated_by=graph.user_id
            )
        )
        await sync_agent_delegations(seed_db, context, parent.id, added={("agent", child.id)})
        await seed_db.commit()
        parent_id = parent.id
        assert len(await link_grants(seed_db, {parent_id})) == 1

    try:
        async with pg_factory() as executor_db, pg_factory() as delete_db:
            # The executor's canonical prefix: Organization FOR SHARE, before lifecycle.
            await executor_db.execute(
                select(Organization.id)
                .where(Organization.id == TEST_ORG_ID)
                .with_for_update(read=True)
            )
            agent = await delete_db.get(Agent, graph.agent_id)
            assert agent is not None
            delete_pid = await _backend_pid(delete_db)
            attempted = anyio.Event()
            finished = anyio.Event()

            async def archive() -> None:
                attempted.set()
                await agent_service.delete_agent(delete_db, agent, context=context)
                await delete_db.commit()
                finished.set()

            # When: the archive waits behind the executor, which then takes the lifecycle lock.
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(archive)
                await attempted.wait()
                await _wait_for_lock(executor_db, delete_pid)
                with anyio.fail_after(5):
                    await acquire_agent_lifecycle_locks(executor_db, (graph.agent_id,))
                assert not finished.is_set()
                await executor_db.commit()
                with anyio.fail_after(5):
                    await finished.wait()

        # Then: the archive committed and revoked the parent's delegation.
        async with pg_factory() as verify_db:
            archived = await verify_db.get(Agent, graph.agent_id)
            assert archived is not None and archived.archived_at is not None
            assert await link_grants(verify_db, {parent_id}) == []
    finally:
        async with pg_factory() as cleanup_db:
            grant_ids = select(ResourceGrant.id).where(ResourceGrant.principal_id == parent_id)
            await cleanup_db.execute(
                delete(AuthzOutbox).where(AuthzOutbox.source_id.in_(grant_ids))
            )
            await cleanup_db.execute(
                delete(ResourceGrant).where(ResourceGrant.principal_id == parent_id)
            )
            await cleanup_db.execute(
                delete(AgentSubAgentLink).where(
                    or_(
                        AgentSubAgentLink.parent_agent_id == parent_id,
                        AgentSubAgentLink.sub_agent_id == parent_id,
                    )
                )
            )
            await cleanup_db.execute(delete(Agent).where(Agent.id == parent_id))
            await cleanup_db.commit()
