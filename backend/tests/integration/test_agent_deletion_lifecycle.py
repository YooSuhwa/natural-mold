from __future__ import annotations

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import agent_service
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
        await seed_db.commit()

    async with pg_factory() as worker_db, pg_factory() as delete_db:
        await worker_db.get(ConversationRun, run_id, with_for_update=True)
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None

        # When: deletion overlaps the worker's terminal persistence transaction.
        with anyio.fail_after(2), pytest.raises(HTTPException) as raised:
            await agent_service.delete_agent(delete_db, agent)

        # Then: reject before cascade needs the locked run; never wait on it.
        assert raised.value.status_code == 409
        await delete_db.rollback()
        await worker_db.rollback()

    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)


async def test_run_creation_first_fences_agent_delete_until_commit(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: run creation holds its conversation fence before committing.
    async with pg_factory() as create_db, pg_factory() as delete_db:
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        delete_pid = await _backend_pid(delete_db)
        await _create_run(create_db, graph)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def remove() -> None:
            attempted.set()
            with pytest.raises(HTTPException) as raised:
                await agent_service.delete_agent(delete_db, agent)
            assert raised.value.status_code == 409
            await delete_db.rollback()
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

    # Then: the committed run and its ownership graph survive deletion rejection.
    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)


async def test_agent_delete_first_fences_run_creation_then_returns_not_found(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: deletion owns the conversation fence before run creation begins.
    async with pg_factory() as delete_db, pg_factory() as create_db:
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        await delete_db.execute(
            select(Conversation).where(Conversation.id == graph.conversation_id).with_for_update()
        )
        create_pid = await _backend_pid(create_db)
        attempted = anyio.Event()
        finished = anyio.Event()

        async def create() -> None:
            attempted.set()
            with pytest.raises(HTTPException) as raised:
                await _create_run(create_db, graph)
            assert raised.value.status_code == 404
            await create_db.rollback()
            finished.set()

        # When: the contender requests a run while deletion commits.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(create)
            await attempted.wait()
            await _wait_for_lock(delete_db, create_pid)
            assert not finished.is_set()
            await agent_service.delete_agent(delete_db, agent)
            await delete_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

    # Then: the run contender sees the missing conversation, rather than an FK failure.
