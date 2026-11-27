from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent_runtime.runtime import memory_context
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import live_proof
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.memory import AgentMemorySettings, MemoryRecall, MemoryRecallRun, MemoryRecord
from app.models.model import Model
from app.models.user import User
from app.services import (
    conversation_run_service,
    memory_embedder,
    memory_recall,
    memory_service,
    user_service,
)
from app.services.chat import conversations as conversation_service
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)


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
        await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
        agent = Agent(
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name="Race Agent",
            system_prompt="x",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conversation = Conversation(
            agent_id=agent.id,
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_name_snapshot=agent.name,
            last_activity_at=datetime.now(UTC),
            title="Race Conversation",
        )
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
        context=unit_context(graph.user_id),
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
        await conversation_service.delete_conversation(
            delete_db, conversation, context=unit_context(graph.user_id)
        )
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
            await conversation_service.delete_conversation(
                delete_db, conversation, context=unit_context(graph.user_id)
            )

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
async def test_user_delete_first_returns_transfer_conflict_then_run_succeeds(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as delete_db, pg_factory() as create_db:
        create_pid = await _backend_pid(create_db)
        await delete_db.execute(select(User).where(User.id == graph.user_id).with_for_update())
        attempted = anyio.Event()
        finished = anyio.Event()
        created_runs: list[ConversationRun] = []

        async def create() -> None:
            attempted.set()
            try:
                run = await _create_run(create_db, graph)
                await create_db.commit()
                created_runs.append(run)
            finally:
                finished.set()

        async def rollback_delete() -> None:
            with anyio.CancelScope(shield=True):
                with anyio.fail_after(5):
                    await delete_db.rollback()

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                try:
                    tasks.start_soon(create)
                    with anyio.fail_after(5):
                        await attempted.wait()
                    await _wait_for_lock(delete_db, create_pid)
                    assert not finished.is_set()
                    with pytest.raises(AppError) as raised:
                        await user_service.delete_user(delete_db, graph.user_id)
                    await rollback_delete()
                    assert raised.value.code == "resource_transfer_required"
                    assert raised.value.status == 409
                    with anyio.fail_after(5):
                        await finished.wait()
                finally:
                    await rollback_delete()

    assert len(created_runs) == 1
    assert cleanup_calls == CleanupCalls()
    async with pg_factory() as verify_db:
        stored_user = await verify_db.get(User, graph.user_id)
        stored_agent = await verify_db.get(Agent, graph.agent_id)
        stored_conversation = await verify_db.get(Conversation, graph.conversation_id)
        stored_runs = (
            await verify_db.scalars(
                select(ConversationRun).where(
                    ConversationRun.conversation_id == graph.conversation_id
                )
            )
        ).all()

    assert stored_user is not None
    assert stored_agent is not None
    assert stored_agent.user_id == graph.user_id
    assert stored_agent.org_id == TEST_ORG_ID
    assert stored_agent.tenant_id == TEST_TENANT_ID
    assert stored_conversation is not None
    assert stored_conversation.user_id == graph.user_id
    assert stored_conversation.agent_id == graph.agent_id
    assert stored_conversation.org_id == TEST_ORG_ID
    assert stored_conversation.tenant_id == TEST_TENANT_ID
    assert len(stored_runs) == 1
    stored_run = stored_runs[0]
    assert stored_run.id == created_runs[0].id
    assert stored_run.user_id == graph.user_id
    assert stored_run.agent_id == graph.agent_id
    assert stored_run.conversation_id == graph.conversation_id
    assert stored_run.source == "chat"
    assert stored_run.status == "queued"
    assert stored_run.is_active is True


@pytest.mark.asyncio
async def test_run_first_user_delete_refuses_resource_transfer_and_preserves_graph(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    cleanup_calls: CleanupCalls,
) -> None:
    async with pg_factory() as create_db, pg_factory() as delete_db:
        created_run = await _create_run(create_db, graph)
        attempted = anyio.Event()
        finished = anyio.Event()
        delete_errors: list[AppError] = []

        async def remove() -> None:
            attempted.set()
            try:
                await user_service.delete_user(delete_db, graph.user_id)
            except AppError as exc:
                delete_errors.append(exc)
            finally:
                finished.set()

        async def rollback_session(db: AsyncSession) -> None:
            with anyio.CancelScope(shield=True):
                with anyio.fail_after(5):
                    await db.rollback()

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                try:
                    tasks.start_soon(remove)
                    with anyio.fail_after(5):
                        await attempted.wait()
                    # E-02 (fcdcda1c): owned resources refuse before any account lock, so
                    # the uncommitted run no longer blocks the delete; it answers 409 at once.
                    with anyio.fail_after(5):
                        await finished.wait()
                    await create_db.commit()
                finally:
                    await rollback_session(create_db)
                    if attempted.is_set() and not finished.is_set():
                        with anyio.CancelScope(shield=True):
                            with anyio.fail_after(5):
                                await finished.wait()
                    await rollback_session(delete_db)

    assert cleanup_calls == CleanupCalls()
    assert len(delete_errors) == 1
    assert delete_errors[0].code == "resource_transfer_required"
    assert delete_errors[0].status == 409
    async with pg_factory() as verify_db:
        stored_user = await verify_db.get(User, graph.user_id)
        stored_agent = await verify_db.get(Agent, graph.agent_id)
        stored_conversation = await verify_db.get(Conversation, graph.conversation_id)
        stored_runs = (
            await verify_db.scalars(
                select(ConversationRun).where(
                    ConversationRun.conversation_id == graph.conversation_id
                )
            )
        ).all()

    assert stored_user is not None
    assert stored_agent is not None
    assert stored_agent.user_id == graph.user_id
    assert stored_agent.org_id == TEST_ORG_ID
    assert stored_agent.tenant_id == TEST_TENANT_ID
    assert stored_conversation is not None
    assert stored_conversation.user_id == graph.user_id
    assert stored_conversation.agent_id == graph.agent_id
    assert stored_conversation.org_id == TEST_ORG_ID
    assert stored_conversation.tenant_id == TEST_TENANT_ID
    assert len(stored_runs) == 1
    stored_run = stored_runs[0]
    assert stored_run.id == created_run.id
    assert stored_run.user_id == graph.user_id
    assert stored_run.agent_id == graph.agent_id
    assert stored_run.conversation_id == graph.conversation_id
    assert stored_run.source == "chat"
    assert stored_run.status == "queued"
    assert stored_run.is_active is True


@pytest.mark.asyncio
async def test_run_creation_user_fence_allows_memory_settings_insert_before_commit(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
) -> None:
    """A pending run must not block runtime memory-policy initialization.

    ``create_run`` fences the user against a concurrent delete until its
    transaction commits. Runtime component assembly uses another session to
    lazily create the user's memory settings, which needs PostgreSQL's
    ``KEY SHARE`` foreign-key lock on that user row. The create fence must be
    compatible with that insert while still blocking a delete.
    """
    # A blocked insert waits forever, so the memory session gets a lock_timeout
    # instead of a wall-clock deadline: only a lock wait fails the test, not a slow runner.
    bind = pg_factory.kw["bind"]
    memory_engine = create_async_engine(
        bind.url, connect_args={"server_settings": {"lock_timeout": "2s"}}
    )
    memory_factory = async_sessionmaker(memory_engine, expire_on_commit=False)
    try:
        async with pg_factory() as create_db, memory_factory() as memory_db:
            # Given: create_run has acquired its user/conversation fences but has
            # not committed the new run yet.
            await _create_run(create_db, graph)

            # When: the separately scoped runtime-memory session resolves its
            # policy, lazily creating default user settings.
            with anyio.fail_after(30):
                policy = await memory_service.resolve_effective_policy(
                    memory_db,
                    context=unit_context(graph.user_id),
                    agent_id=graph.agent_id,
                )

            # Then: the FK-backed insert completes before create_run commits.
            assert policy.read_enabled is True
            await create_db.rollback()
    finally:
        await memory_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commit", "ranking_timeout"),
    [(False, False), (True, False), (True, True)],
    ids=["rollback", "commit", "ranking-timeout"],
)
async def test_prepared_recall_shares_the_locked_runs_transaction(
    pg_factory: async_sessionmaker[AsyncSession],
    graph: SeededGraph,
    monkeypatch: pytest.MonkeyPatch,
    commit: bool,
    ranking_timeout: bool,
) -> None:
    context = unit_context(graph.user_id)

    @asynccontextmanager
    async def reader_for(_context: OrgContext) -> AsyncGenerator[AsyncSession, None]:
        async with pg_factory() as reader:
            await reader.execute(text("SET LOCAL lock_timeout = '2s'"))
            yield reader

    # The default test sessions point to SQLite. Exercise every read and write on PostgreSQL.
    monkeypatch.setattr(memory_context, "recall_session", reader_for)
    monkeypatch.setattr(memory_embedder, "async_session", pg_factory)
    if ranking_timeout:

        async def exceed_reader_timeout(
            reader: AsyncSession, *_args: object, **_kwargs: object
        ) -> None:
            await reader.execute(text("SET LOCAL statement_timeout = '30ms'"))
            await reader.execute(text("SELECT pg_sleep(2)"))
            pytest.fail("PostgreSQL should have canceled the ranking query")

        monkeypatch.setattr(memory_recall, "rank", exceed_reader_timeout)
    fact = "세션 만료 시간은 30분이다"
    async with pg_factory() as db:
        record = MemoryRecord(
            user_id=graph.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            scope="user",
            content=fact,
            store_path="user/session-expiry",
        )
        db.add_all(
            [record, AgentMemorySettings(agent_id=graph.agent_id, memory_policy_override="off")]
        )
        run = await _create_run(db, graph)
        run.input_preview = "세션 만료 시간 알려줘"
        await db.commit()
    cfg = AgentConfig(
        provider="openai",
        model_name="unused",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        org_context=context,
        thread_id=str(graph.conversation_id),
        user_id=str(graph.user_id),
        agent_id=str(graph.agent_id),
        memory_run_id=str(run.id),
    )
    receipts = select(MemoryRecallRun.mode).where(MemoryRecallRun.run_id == str(run.id))
    recalled = select(MemoryRecall.record_id).where(MemoryRecall.run_id == str(run.id))
    target = RunExecutionTarget(
        run.id, RunConversationTarget(graph.conversation_id, graph.agent_id, context)
    )
    async with pg_factory() as owner, pg_factory() as observer:
        locked_run = await lock_executable_run(owner, target)
        assert locked_run is not None
        proof = live_proof(owner)
        assert proof is not None
        await owner.execute(text("SET LOCAL statement_timeout = '23s'"))
        # A separate insert needs a foreign-key lock on this already locked conversation.
        with anyio.fail_after(15):
            _, briefs = await memory_context.load_memory_context_in(cfg, owner)
        expected_ids = [] if ranking_timeout else [str(record.id)]
        assert [brief["id"] for brief in briefs] == expected_ids
        assert (fact in (cfg.memory_recall_block or "")) is (not ranking_timeout)
        if commit and not ranking_timeout:
            # The reader cannot see pending writes. A second prepare in this transaction
            # must still re-use the exact selected record and its original receipt.
            _, repeated = await memory_context.load_memory_context_in(cfg, owner)
            assert repeated == briefs
            await conversation_run_service.transition_run(owner, locked_run, "running")
            await conversation_run_service.transition_run(
                owner, locked_run, "interrupted", interrupt_id="approval"
            )
        assert live_proof(owner) is proof
        assert await owner.scalar(text("SHOW statement_timeout")) == "23s"
        assert await owner.scalar(receipts) == ("fallback" if ranking_timeout else "fresh")
        assert await observer.scalar(receipts) is None
        if commit:
            await owner.commit()
        else:
            await owner.rollback()
        assert len((await observer.scalars(receipts)).all()) == int(commit)
        assert list(await observer.scalars(recalled)) == (
            [record.id] if commit and not ranking_timeout else []
        )
        assert await observer.scalar(
            select(MemoryRecord.recall_count).where(MemoryRecord.id == record.id)
        ) == int(commit and not ranking_timeout)

    if commit and not ranking_timeout:
        await _assert_resume_reader_failure_preserves_receipt(
            pg_factory, cfg, target, record.id, monkeypatch
        )


async def _assert_resume_reader_failure_preserves_receipt(
    pg_factory: async_sessionmaker[AsyncSession],
    cfg: AgentConfig,
    target: RunExecutionTarget,
    record_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_factory() as db:
        resumed = await conversation_run_service.create_run(
            db,
            conversation_id=target.conversation.conversation_id,
            agent_id=target.conversation.agent_id,
            context=cfg.org_context,
            source="resume",
            parent_run_id=target.run_id,
            interrupt_id="approval",
            input_preview=None,
        )
        await db.commit()
    failed_read = False

    @asynccontextmanager
    async def unavailable_reader(_context: OrgContext) -> AsyncGenerator[AsyncSession, None]:
        nonlocal failed_read
        failed_read = True
        async with pg_factory() as reader:
            await reader.execute(text("SELECT 1 / 0"))
            yield reader

    monkeypatch.setattr(memory_context, "recall_session", unavailable_reader)
    cfg.memory_run_id = str(resumed.id)
    async with pg_factory() as owner:
        assert await lock_executable_run(owner, RunExecutionTarget(resumed.id, target.conversation))
        _, briefs = await memory_context.load_memory_context_in(cfg, owner)
        assert failed_read and not briefs and not cfg.memory_recall_block
        await owner.commit()
    async with pg_factory() as check:
        chain = (str(target.run_id), str(resumed.id))
        assert list(
            await check.scalars(
                select(MemoryRecallRun.mode).where(MemoryRecallRun.run_id.in_(chain))
            )
        ) == ["fresh"]
        assert list(
            await check.scalars(
                select(MemoryRecall.record_id).where(MemoryRecall.run_id.in_(chain))
            )
        ) == [record_id]
