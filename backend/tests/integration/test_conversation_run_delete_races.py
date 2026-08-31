from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service, user_service
from app.services.chat import conversations as conversation_service


@dataclass(frozen=True, slots=True)
class SeededGraph:
    user_id: uuid.UUID
    model_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass(slots=True)
class CleanupCalls:
    checkpoint_threads: list[str] = field(default_factory=list)
    offload_scopes: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
async def pg_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    raw_url = os.environ.get("DATABASE_URL")
    if raw_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL race tests")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "moldy_pg_lane_"
    ):
        pytest.skip("race tests require the disposable PostgreSQL lane")

    engine = create_async_engine(url, pool_size=5, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def cleanup_calls(monkeypatch: pytest.MonkeyPatch) -> CleanupCalls:
    calls = CleanupCalls()

    async def record_checkpoint(thread_id: str) -> None:
        calls.checkpoint_threads.append(thread_id)

    def record_offload(
        _data_dir: Path,
        *,
        owner_id: str,
        conversation_id: str,
    ) -> None:
        calls.offload_scopes.append((owner_id, conversation_id))

    monkeypatch.setattr("app.agent_runtime.checkpointer.delete_thread", record_checkpoint)
    monkeypatch.setattr(
        "app.agent_runtime.offload_storage.delete_conversation_offloads",
        record_offload,
    )
    return calls


async def _seed(factory: async_sessionmaker[AsyncSession]) -> SeededGraph:
    async with factory() as db:
        user = User(email=f"delete-race-{uuid.uuid4().hex}@test.dev", name="Race User")
        model = Model(provider="openai", model_name="race-model", display_name="Race Model")
        db.add_all([user, model])
        await db.flush()
        agent = Agent(user_id=user.id, name="Race Agent", system_prompt="x", model_id=model.id)
        db.add(agent)
        await db.flush()
        conversation = Conversation(agent_id=agent.id, title="Race Conversation")
        db.add(conversation)
        await db.commit()
        return SeededGraph(user.id, model.id, agent.id, conversation.id)


@pytest.fixture
async def graph(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[SeededGraph, None]:
    seeded = await _seed(pg_factory)
    yield seeded
    async with pg_factory() as db:
        await db.execute(
            delete(ConversationRun).where(ConversationRun.conversation_id == seeded.conversation_id)
        )
        await db.execute(delete(Conversation).where(Conversation.id == seeded.conversation_id))
        await db.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await db.execute(delete(User).where(User.id == seeded.user_id))
        await db.execute(delete(Model).where(Model.id == seeded.model_id))
        await db.commit()


async def _backend_pid(db: AsyncSession) -> int:
    return int((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())


async def _wait_for_lock(holder: AsyncSession, waiter_pid: int) -> None:
    with anyio.fail_after(5):
        while True:
            wait_type = await holder.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": waiter_pid},
            )
            if wait_type == "Lock":
                return
            await anyio.sleep(0.01)


async def _create_run(db: AsyncSession, graph: SeededGraph) -> ConversationRun:
    return await conversation_run_service.create_run(
        db,
        conversation_id=graph.conversation_id,
        agent_id=graph.agent_id,
        user_id=graph.user_id,
        source="chat",
        input_preview="race",
    )


async def _assert_graph(
    factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    *,
    user_present: bool,
    conversation_present: bool,
) -> None:
    async with factory() as db:
        assert (await db.get(User, graph.user_id) is not None) is user_present
        assert (
            await db.get(Conversation, graph.conversation_id) is not None
        ) is conversation_present
        run_count = len(
            (
                await db.scalars(
                    select(ConversationRun).where(
                        ConversationRun.conversation_id == graph.conversation_id
                    )
                )
            ).all()
        )
        assert run_count == (1 if conversation_present else 0)


@pytest.mark.asyncio
async def test_conversation_delete_first_blocks_run_then_returns_not_found(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as delete_db, pg_factory() as create_db:
        conversation = await delete_db.get(Conversation, graph.conversation_id)
        assert conversation is not None
        create_pid = await _backend_pid(create_db)
        await delete_db.execute(
            select(Conversation).where(Conversation.id == graph.conversation_id).with_for_update()
        )
        attempted = asyncio.Event()

        async def create() -> ConversationRun:
            attempted.set()
            return await _create_run(create_db, graph)

        task = asyncio.create_task(create())
        await attempted.wait()
        await _wait_for_lock(delete_db, create_pid)
        assert not task.done()
        await conversation_service.delete_conversation(delete_db, conversation)
        await delete_db.commit()
        with pytest.raises(HTTPException) as raised:
            await asyncio.wait_for(task, timeout=5)
        assert raised.value.status_code == 404
        await create_db.rollback()

    assert cleanup_calls.checkpoint_threads == [str(graph.conversation_id)]
    assert cleanup_calls.offload_scopes == [(str(graph.user_id), str(graph.conversation_id))]
    await _assert_graph(
        pg_factory,
        graph,
        user_present=True,
        conversation_present=False,
    )


@pytest.mark.asyncio
async def test_run_first_blocks_conversation_delete_then_returns_conflict(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as create_db, pg_factory() as delete_db:
        conversation = await delete_db.get(Conversation, graph.conversation_id)
        assert conversation is not None
        delete_pid = await _backend_pid(delete_db)
        await _create_run(create_db, graph)
        attempted = asyncio.Event()

        async def remove() -> None:
            attempted.set()
            await conversation_service.delete_conversation(delete_db, conversation)

        task = asyncio.create_task(remove())
        await attempted.wait()
        await _wait_for_lock(create_db, delete_pid)
        assert not task.done()
        await create_db.commit()
        with pytest.raises(HTTPException) as raised:
            await asyncio.wait_for(task, timeout=5)
        assert raised.value.status_code == 409
        await delete_db.rollback()

    assert cleanup_calls == CleanupCalls()
    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)


@pytest.mark.asyncio
async def test_user_delete_first_blocks_run_then_returns_not_found(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as delete_db, pg_factory() as create_db:
        create_pid = await _backend_pid(create_db)
        await delete_db.execute(select(User).where(User.id == graph.user_id).with_for_update())
        attempted = asyncio.Event()

        async def create() -> ConversationRun:
            attempted.set()
            return await _create_run(create_db, graph)

        task = asyncio.create_task(create())
        await attempted.wait()
        await _wait_for_lock(delete_db, create_pid)
        assert not task.done()
        await user_service.delete_user(delete_db, graph.user_id)
        await delete_db.commit()
        with pytest.raises(HTTPException) as raised:
            await asyncio.wait_for(task, timeout=5)
        assert raised.value.status_code == 404
        await create_db.rollback()

    assert cleanup_calls.checkpoint_threads == [str(graph.conversation_id)]
    assert cleanup_calls.offload_scopes == [(str(graph.user_id), str(graph.conversation_id))]
    await _assert_graph(pg_factory, graph, user_present=False, conversation_present=False)


@pytest.mark.asyncio
async def test_run_first_blocks_user_delete_then_preserves_graph(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as create_db, pg_factory() as delete_db:
        delete_pid = await _backend_pid(delete_db)
        await _create_run(create_db, graph)
        attempted = asyncio.Event()

        async def remove() -> None:
            attempted.set()
            await user_service.delete_user(delete_db, graph.user_id)

        task = asyncio.create_task(remove())
        await attempted.wait()
        await _wait_for_lock(create_db, delete_pid)
        assert not task.done()
        await create_db.commit()
        with pytest.raises(user_service.ActiveConversationRunConflict):
            await asyncio.wait_for(task, timeout=5)
        await delete_db.rollback()

    assert cleanup_calls == CleanupCalls()
    await _assert_graph(pg_factory, graph, user_present=True, conversation_present=True)
