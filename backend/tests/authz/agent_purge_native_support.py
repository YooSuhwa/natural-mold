"""Real PostgreSQL/JWT helpers for Task 14 purge acceptance nodes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import (
    get_checkpointer,
    init_checkpointer,
    shutdown_checkpointer,
)
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.services import conversation_run_service
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture


@asynccontextmanager
async def native_checkpointer() -> AsyncIterator[None]:
    await init_checkpointer(settings.database_url_sync)
    try:
        yield
    finally:
        await shutdown_checkpointer()


@asynccontextmanager
async def actor_client(
    fixture: RegisteredLifecycleFixture,
    actor_id: UUID,
) -> AsyncIterator[tuple[AsyncClient, str]]:
    async with fixture.sessions() as db:
        actor = await db.get(User, actor_id)
        assert actor is not None
        email = actor.email
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with fixture.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": email, "password": "fixture password 42"},
        )
        assert login.status_code == 200, login.text
        yield client, login.json()["csrf_token"]


async def seed_missing_purge_checkpoints(fixture: RegisteredLifecycleFixture) -> None:
    saver = get_checkpointer()
    for index, conversation_id in enumerate(fixture.graph.conversation_ids):
        config: RunnableConfig = {
            "configurable": {"thread_id": str(conversation_id), "checkpoint_ns": ""}
        }
        if await saver.aget_tuple(config) is not None:
            continue
        checkpoint = empty_checkpoint()
        checkpoint["id"] = f"task14-{conversation_id.hex}"
        checkpoint["channel_values"] = {
            "messages": [HumanMessage(id=f"task14-message-{index}", content=f"history-{index}")]
        }
        checkpoint["channel_versions"] = {"messages": "1"}
        await saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": 0},
            {"messages": "1"},
        )


async def acknowledge_archived_runs(fixture: RegisteredLifecycleFixture) -> None:
    async with fixture.sessions() as db:
        for run_id in fixture.graph.run_ids:
            run = await db.get(ConversationRun, run_id, with_for_update=True)
            assert run is not None and run.status == "canceling"
            await conversation_run_service.transition_run(
                db,
                run,
                "canceled",
                cancellation_ack_worker_id=run.worker_instance_id,
                allow_workerless_cancellation_ack=run.worker_instance_id is None,
            )
        await db.commit()


__all__ = [
    "acknowledge_archived_runs",
    "actor_client",
    "native_checkpointer",
    "seed_missing_purge_checkpoints",
]
