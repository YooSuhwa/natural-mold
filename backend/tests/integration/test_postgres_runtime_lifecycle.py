"""Focused real-PostgreSQL resource ownership probe."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_runtime_resources_close_after_real_postgres_use() -> None:
    # Given the runner-configured engine and explicit runtime resource owners.
    from app import database
    from app.agent_runtime import checkpointer
    from app.config import settings
    from app.services.conversation_run_worker import RunTaskRegistry
    from app.services.skill_evaluation_worker import SkillEvaluationWorker

    worker = SkillEvaluationWorker()
    registry = RunTaskRegistry(worker_instance_id="postgres-lifecycle-probe")

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
