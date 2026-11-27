"""Two detached workers race the same durable AG-UI command in PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent_runtime import checkpointer, langgraph_agent_stream_runner
from app.agent_runtime.runtime_config import AgentConfig
from app.config import settings
from app.dependencies import CurrentUser
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import (
    conversation_run_queue_worker,
    conversation_run_worker,
    conversation_stream_service,
)
from app.services.ag_ui_deferred_delivery import dispatch_ag_ui_run
from app.services.conversation_run_task_registry import RunTaskRegistry
from tests.ag_ui_deferred_testkit import seed_deferred
from tests.authz.organization_migration_fixture import async_organization_lab
from tests.authz.unit_scope_fixture import ensure_unit_scope

pytestmark = pytest.mark.integration


async def test_two_workers_dispatch_one_private_command(monkeypatch: pytest.MonkeyPatch) -> None:
    saver = InMemorySaver()
    monkeypatch.setattr(checkpointer, "_checkpointer", saver)
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)
    actor = CurrentUser(id=uuid4(), email="race@example.org", name="Race")
    registries = [RunTaskRegistry(worker_instance_id=f"worker-{name}") for name in ("a", "b")]
    entered = asyncio.Event()
    finish = asyncio.Event()
    executions: list[tuple[UUID, str, str]] = []

    async def graph_stream(
        cfg: AgentConfig, *, run_id: str, moldy_source: str, **_kwargs: Any
    ) -> AsyncIterator[str]:
        executions.append((UUID(run_id), cfg.thread_id, moldy_source))
        entered.set()
        await finish.wait()
        yield 'event: message_end\ndata: {"content":"finished","usage":{}}\n\n'

    monkeypatch.setattr(langgraph_agent_stream_runner, "_run_langgraph_agent_stream", graph_stream)
    # Hold rehydrated dispatches at the normal worker entry. Both processes must
    # contend for this run even if one PostgreSQL connection is much faster.
    both_rehydrated = asyncio.Barrier(len(registries))
    start = conversation_run_queue_worker.start_conversation_run

    async def start_together(**kwargs: Any) -> conversation_stream_service.StreamCtx:
        await both_rehydrated.wait()
        return await start(**kwargs)

    monkeypatch.setattr(conversation_run_queue_worker, "start_conversation_run", start_together)
    async with async_organization_lab(at_head=True) as lab:
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        for module in (
            conversation_run_worker,
            conversation_stream_service,
            conversation_run_queue_worker,
        ):
            monkeypatch.setattr(module, "async_session", sessions)
        try:
            async with sessions() as db:
                context = await ensure_unit_scope(db, actor)
                case = await seed_deferred(db, context, saver=saver)
                await db.commit()

            async with asyncio.timeout(30):
                await asyncio.gather(
                    *(dispatch_ag_ui_run(case.run_id, registry=registry) for registry in registries)
                )
                await entered.wait()
                async with sessions() as db:
                    running = await db.get(ConversationRun, case.run_id)
                    assert running is not None and running.status == "running"
                    assert running.worker_instance_id in {r.worker_instance_id for r in registries}
                tasks = [task for r in registries if (task := r.get(case.run_id)) is not None]
                finish.set()
                await asyncio.gather(*tasks)
                await dispatch_ag_ui_run(case.run_id, registry=registries[1])

            assert executions == [(case.run_id, str(case.conversation_id), "regenerate")]
            async with sessions() as db:
                command_runs = select(ConversationRun).where(
                    ConversationRun.conversation_id == case.conversation_id,
                    ConversationRun.source == "regenerate",
                )
                assert [(row.id, row.status) for row in await db.scalars(command_runs)] == [
                    (case.run_id, "completed")
                ]
                assert (
                    await db.scalar(
                        select(func.count(ConversationRunInput.id)).where(
                            ConversationRunInput.conversation_id == case.conversation_id
                        )
                    )
                    == 0
                )
        finally:
            finish.set()
            await asyncio.gather(*(r.shutdown() for r in registries))
            await engine.dispose()
