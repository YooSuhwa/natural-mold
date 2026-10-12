"""Focused real-PostgreSQL resource ownership probe."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_runtime_resources_close_after_real_postgres_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the runner-configured engine and explicit runtime resource owners.
    from app import database
    from app.agent_runtime import checkpointer
    from app.config import settings
    from app.services import (
        conversation_run_worker,
        conversation_stream_service,
        skill_evaluation_worker,
        skill_evaluation_worker_leader,
    )
    from app.services.conversation_run_worker import RunTaskRegistry
    from app.services.skill_evaluation_worker import SkillEvaluationWorker

    owned_engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    owned_sessions = async_sessionmaker(
        owned_engine,
        class_=database.ShieldedAsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database, "engine", owned_engine)
    monkeypatch.setattr(database, "async_session", owned_sessions)
    monkeypatch.setattr(conversation_run_worker, "async_session", owned_sessions)
    monkeypatch.setattr(conversation_stream_service, "async_session", owned_sessions)
    monkeypatch.setattr(skill_evaluation_worker, "async_session", owned_sessions)
    monkeypatch.setattr(skill_evaluation_worker_leader, "engine", owned_engine)

    worker = SkillEvaluationWorker()
    registry = RunTaskRegistry(worker_instance_id="postgres-lifecycle-probe")

    try:
        # When each resource is opened and closed through its public lifecycle.
        async with database.engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        await checkpointer.init_checkpointer(settings.database_url_sync, min_size=1, max_size=1)
        await checkpointer.shutdown_checkpointer()
        task = asyncio.create_task(asyncio.sleep(30))
        registry.start(__import__("uuid").uuid4(), task)
        await registry.shutdown(timeout_seconds=1)
        assert await worker.start(session_factory=database.async_session) is True
        await worker.stop(timeout_seconds=1)
        await database.shutdown_database()

        # Then no owner retains a live resource.
        assert checkpointer._pool is None
        assert checkpointer._checkpointer is None
        assert registry.active_run_ids() == set()
        assert worker._task is None
    finally:
        try:
            await worker.stop(timeout_seconds=1)
        finally:
            try:
                await registry.shutdown(timeout_seconds=1)
            finally:
                try:
                    await checkpointer.shutdown_checkpointer()
                finally:
                    await database.shutdown_database()
