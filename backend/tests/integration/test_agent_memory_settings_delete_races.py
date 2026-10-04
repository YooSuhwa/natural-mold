from __future__ import annotations

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.memory import AgentMemorySettings
from app.services import agent_service, memory_service
from tests.integration.test_conversation_run_delete_races import (
    SeededGraph,
    _backend_pid,
    _wait_for_lock,
)
from tests.integration.test_conversation_run_delete_races import graph as graph
from tests.integration.test_conversation_run_delete_races import pg_factory as pg_factory


async def test_agent_delete_first_makes_settings_read_return_missing(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: deletion has reached the agent row before lazy settings creation.
    async with pg_factory() as delete_db, pg_factory() as settings_db:
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        await agent_service.delete_agent(delete_db, agent)
        settings_pid = await _backend_pid(settings_db)
        attempted, finished = anyio.Event(), anyio.Event()
        results: list[AgentMemorySettings | None] = []

        async def read_settings() -> None:
            attempted.set()
            results.append(
                await memory_service.get_agent_settings(settings_db, graph.agent_id, graph.user_id)
            )
            finished.set()

        # When: GET overlaps the uncommitted deletion, then deletion commits.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(read_settings)
            await attempted.wait()
            await _wait_for_lock(delete_db, settings_pid)
            assert not finished.is_set()
            await delete_db.commit()
            with anyio.fail_after(5):
                await finished.wait()

        # Then: the router can return its normal 404 without an orphan INSERT.
        assert results == [None]


async def test_settings_read_first_fences_delete_until_settings_commit(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a first GET is paused immediately before its durable commit.
    async with pg_factory() as settings_db, pg_factory() as delete_db:
        before_commit, release_commit, read_finished = anyio.Event(), anyio.Event(), anyio.Event()
        delete_finished = anyio.Event()
        results: list[AgentMemorySettings | None] = []
        original_commit = settings_db.commit

        async def held_commit() -> None:
            before_commit.set()
            await release_commit.wait()
            await original_commit()

        monkeypatch.setattr(settings_db, "commit", held_commit)
        agent = await delete_db.get(Agent, graph.agent_id)
        assert agent is not None
        delete_pid = await _backend_pid(delete_db)

        async def read_settings() -> None:
            results.append(
                await memory_service.get_agent_settings(settings_db, graph.agent_id, graph.user_id)
            )
            read_finished.set()

        async def remove() -> None:
            await agent_service.delete_agent(delete_db, agent)
            await delete_db.commit()
            delete_finished.set()

        # When: deletion races the settings creation transaction.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(read_settings)
            with anyio.fail_after(5):
                await before_commit.wait()
            tasks.start_soon(remove)
            await _wait_for_lock(settings_db, delete_pid)
            assert not delete_finished.is_set()
            release_commit.set()
            with anyio.fail_after(5):
                await read_finished.wait()
                await delete_finished.wait()

        # Then: GET returns initialized defaults and DELETE succeeds afterward.
        assert len(results) == 1
        settings = results[0]
        assert settings is not None
        assert settings.memory_policy_override == "inherit"
        assert settings.memory_scopes_override == "inherit"
        assert settings.trigger_memory_policy_override == "inherit"
