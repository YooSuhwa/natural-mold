from __future__ import annotations

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.memory import AgentMemorySettings
from app.services import agent_service, memory_service
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_conversation_run_delete_races import (
    SeededGraph,
    _backend_pid,
    _wait_for_lock,
)
from tests.integration.test_conversation_run_delete_races import graph as graph
from tests.integration.test_conversation_run_delete_races import pg_factory as pg_factory


async def test_archive_first_serializes_settings_read_and_retains_configuration(
    pg_factory: async_sessionmaker[AsyncSession], graph: SeededGraph
) -> None:
    # Given: archive has reached the agent row before lazy settings creation.
    async with pg_factory() as archive_db, pg_factory() as settings_db:
        agent = await archive_db.get(Agent, graph.agent_id)
        assert agent is not None
        await agent_service.delete_agent(archive_db, agent, context=unit_context(graph.user_id))
        await archive_db.refresh(agent, attribute_names=["archived_at"])
        assert agent.archived_at is not None
        settings_pid = await _backend_pid(settings_db)
        attempted, finished = anyio.Event(), anyio.Event()
        results: list[AgentMemorySettings | None] = []

        async def read_settings() -> None:
            attempted.set()
            try:
                results.append(
                    await memory_service.get_agent_settings(
                        settings_db, graph.agent_id, unit_context(graph.user_id)
                    )
                )
            finally:
                finished.set()

        # When: GET overlaps the uncommitted archive, then archive commits.
        async with anyio.create_task_group() as tasks:
            try:
                tasks.start_soon(read_settings)
                with anyio.fail_after(5):
                    await attempted.wait()
                await _wait_for_lock(archive_db, settings_pid)
                assert not finished.is_set()
                await archive_db.commit()
                with anyio.fail_after(5):
                    await finished.wait()
            finally:
                if not finished.is_set():
                    tasks.cancel_scope.cancel()

    # Then: archive retains history and exactly one initialized settings row.
    assert len(results) == 1
    returned = results[0]
    assert returned is not None
    async with pg_factory() as verify_db:
        stored_agent = await verify_db.get(Agent, graph.agent_id)
        conversation = await verify_db.get(Conversation, graph.conversation_id)
        settings_rows = (
            await verify_db.scalars(
                select(AgentMemorySettings).where(AgentMemorySettings.agent_id == graph.agent_id)
            )
        ).all()

    assert stored_agent is not None and stored_agent.archived_at is not None
    assert conversation is not None
    assert conversation.agent_id == stored_agent.id
    assert conversation.agent_lifecycle_state == "archived"
    assert conversation.agent_name_snapshot == stored_agent.name
    assert len(settings_rows) == 1
    persisted = settings_rows[0]
    assert returned.agent_id == persisted.agent_id == graph.agent_id
    assert returned.memory_policy_override == persisted.memory_policy_override == "inherit"
    assert returned.memory_scopes_override == persisted.memory_scopes_override == "inherit"
    assert (
        returned.trigger_memory_policy_override
        == persisted.trigger_memory_policy_override
        == "inherit"
    )


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
                await memory_service.get_agent_settings(
                    settings_db, graph.agent_id, unit_context(graph.user_id)
                )
            )
            read_finished.set()

        async def remove() -> None:
            await agent_service.delete_agent(delete_db, agent, context=unit_context(graph.user_id))
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
