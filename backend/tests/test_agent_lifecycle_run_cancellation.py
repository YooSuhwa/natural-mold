from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_execution_owner, conversation_run_service, conversation_run_worker
from app.services.conversation_run_worker import RunTaskRegistry
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


@pytest.mark.asyncio
async def test_foreign_owner_never_acknowledges_cancellation(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="foreign",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id="remote-owner"
    )
    await conversation_run_service.request_cancel_run(db, run, reason="agent_archived")

    with pytest.raises(ValueError, match="owning worker"):
        await conversation_run_service.transition_run(
            db, run, "canceled", cancellation_ack_worker_id="foreign-worker"
        )
    assert run.status == "canceling"
    assert run.cancellation_acknowledged_at is None
    await db.rollback()


@pytest.mark.asyncio
async def test_local_registry_absent_leaves_durable_cancel_pending(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="local_registry_absent",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id="remote-owner"
    )
    await db.commit()

    intent = await agent_execution_owner.persist_cancel_intent(
        TestSession,
        agent_execution_owner.CancelIntentRequest(run.id, "agent_archived"),
    )
    registry = RunTaskRegistry(worker_instance_id="remote-owner")
    assert agent_execution_owner.notify_local_owner(intent, registry) is False

    await db.refresh(run)
    assert run.status == "canceling"
    assert run.cancellation_acknowledged_at is None


@pytest.mark.asyncio
async def test_local_owner_shutdown_timeout_leaves_durable_cancel_unacknowledged(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    registry = RunTaskRegistry(worker_instance_id="local-owner")
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="real shutdown timeout",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id=registry.worker_instance_id
    )
    await db.commit()

    intent = await agent_execution_owner.persist_cancel_intent(
        TestSession,
        agent_execution_owner.CancelIntentRequest(run.id, "agent_archived"),
    )
    task_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def held_local_task() -> None:
        task_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.shield(release_cleanup.wait())

    async def skip_shutdown_intent(_run_ids: list[uuid.UUID]) -> None:
        return None

    async def release_held_cleanup() -> None:
        await asyncio.sleep(0.03)
        release_cleanup.set()

    task = asyncio.create_task(held_local_task())
    registry.start(run.id, task)
    await task_started.wait()
    assert agent_execution_owner.notify_local_owner(intent, registry) is True
    await cleanup_started.wait()
    monkeypatch.setattr(conversation_run_worker, "_persist_shutdown_intent", skip_shutdown_intent)
    caplog.set_level(logging.WARNING, logger=conversation_run_worker.__name__)

    release_task = asyncio.create_task(release_held_cleanup())
    await registry.shutdown(timeout_seconds=0.005)
    await release_task
    task_result = await asyncio.gather(task, return_exceptions=True)

    assert "timed out waiting for 1 conversation run task(s) during shutdown" in caplog.text
    assert isinstance(task_result[0], asyncio.CancelledError)
    assert registry.get(run.id) is None
    await db.refresh(run)
    assert run.status == "canceling"
    assert run.worker_instance_id == registry.worker_instance_id
    assert run.cancellation_acknowledged_at is None
