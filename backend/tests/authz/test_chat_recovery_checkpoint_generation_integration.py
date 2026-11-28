"""A lost PostgreSQL checkpoint writer cannot publish across an owner handoff."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, cast
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, DatabaseError, errors
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent_runtime.recovery_checkpoint_saver import RecoveryCheckpointSaver
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_fence import (
    RunFence,
    RunOwnershipLost,
    claim_run_owner,
    lock_run_fence,
)
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


async def _seed_fence(lab: RlsLab) -> RunFence:
    engine = create_async_engine(async_url(lab.admin_url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            graph = await seed_graph(db, conversations=1)
            row = ConversationRun(
                conversation_id=graph.conversation_ids[0],
                agent_id=graph.agent_id,
                user_id=graph.context.user_id,
                org_id=graph.context.org_id,
                tenant_id=graph.context.tenant_id,
                owner_generation=0,
                source="chat",
                status="running",
                is_active=True,
            )
            db.add(row)
            await db.flush()
            fence = claim_run_owner(row, "first")
            await db.commit()
            return fence
    finally:
        await engine.dispose()


async def test_checkpoint_writer_death_rolls_back_data_before_next_generation(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _seed_fence(lab)
    admin_url = lab.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    app_url = lab.app_url.set(drivername="postgresql").render_as_string(hide_password=False)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(first.conversation_id),
            "checkpoint_ns": "",
            "moldy_run_id": str(first.run_id),
            "moldy_tenant_id": str(first.tenant.tenant_id),
        }
    }
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        app_url, min_size=1, max_size=3, open=False, kwargs={"row_factory": dict_row}
    )
    async with pool:
        upstream = AsyncPostgresSaver(pool)
        old = RecoveryCheckpointSaver(upstream, first)
        saved = await old.aput(config, empty_checkpoint(), {"step": 0}, {})

        # Park after the upstream INSERT returned, but before the surrounding
        # transaction commits. Another session must still see the run lock.
        parked: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        release = asyncio.Event()
        real_put = AsyncPostgresSaver.aput

        async def park(saver: AsyncPostgresSaver, *args: Any) -> RunnableConfig:
            result = await real_put(saver, *args)
            connection = cast("AsyncConnection[Any]", saver.conn)
            pid = await (await connection.execute("SELECT pg_backend_pid()")).fetchone()
            assert pid is not None
            parked.set_result(pid["pg_backend_pid"])
            await release.wait()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(AsyncPostgresSaver, "aput", park)
            pending = asyncio.create_task(old.aput(saved, empty_checkpoint(), {"step": 1}, {}))
            try:
                pid = await asyncio.wait_for(parked, 5)
                async with await AsyncConnection.connect(admin_url, autocommit=True) as admin:
                    with pytest.raises(errors.LockNotAvailable):
                        await admin.execute(
                            "SELECT id FROM conversation_runs WHERE id = %s FOR UPDATE NOWAIT",
                            (first.run_id,),
                        )
                    await admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            finally:
                release.set()
                with pytest.raises(DatabaseError):
                    await asyncio.wait_for(pending, 5)

        engine = create_async_engine(async_url(lab.admin_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                row = await lock_run_fence(db, first)
                second = claim_run_owner(row, "second")
                await db.commit()
        finally:
            await engine.dispose()

        new = RecoveryCheckpointSaver(upstream, second)
        head = await new.aget_tuple(config)
        assert head is not None and head.config == saved
        with pytest.raises(RunOwnershipLost):
            await old.aget_tuple(config)
        with pytest.raises(RunOwnershipLost):
            await old.aput(saved, empty_checkpoint(), {"step": 2}, {})
        with pytest.raises(RunOwnershipLost):
            await old.aput_writes(saved, [("value", "stale")], str(uuid4()))
        with pytest.raises(RunOwnershipLost):
            await new.aget_tuple({"configurable": {"thread_id": str(uuid4())}})
        await new.aput_writes(saved, [("value", "current")], str(uuid4()))
        updated = await new.aget_tuple(config)
        assert updated is not None and [w[2] for w in updated.pending_writes or []] == ["current"]
