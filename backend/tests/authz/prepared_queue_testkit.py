"""Queue-side helpers for the prepared-lease queue/recovery tests (internal I19)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    get_prepared_run_registry,
    reset_prepared_run_registry_for_tests,
)
from app.models.conversation_run import ConversationRun
from app.services import (
    conversation_run_queue_service,
    conversation_run_queue_worker,
    conversation_run_worker,
)
from app.services import prepared_conversation_start as start
from app.services.conversation_run_task_registry import RunTaskRegistry
from tests.authz import prepared_entry_testkit as ekit
from tests.conftest import TestSession

PAYLOAD: dict[str, Any] = {"messages": [{"role": "user", "content": "queued hi"}]}


async def queue_harness(db: AsyncSession, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    harness = await ekit.setup(
        db, tmp_path, monkeypatch, [AIMessage("queued done")], with_child=False
    )
    seeded = harness.seeded
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == seeded.run_id)
        .values(status="completed", is_active=False)
    )
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=seeded.request.conversation_id,
        context=seeded.graph.context,
        client_request_id=f"prepared-{uuid.uuid4().hex}",
        source="chat",
        input_payload=PAYLOAD,
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    return harness, queued.id


async def claim(db: AsyncSession, harness: ekit.Harness) -> uuid.UUID:
    seeded = harness.seeded
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=seeded.request.conversation_id, context=seeded.graph.context
    )
    assert claimed is not None and claimed.run.runtime_materialized_at is None
    await db.commit()
    return claimed.run.id


async def await_run(run_id: uuid.UUID) -> ConversationRun:
    task = conversation_run_worker.get_run_task_registry().get(run_id)
    if task is not None:
        await task
    async with TestSession() as check:
        row = await check.get(ConversationRun, run_id)
    assert row is not None
    return row


def restart(monkeypatch: pytest.MonkeyPatch) -> PreparedRunRegistry:
    """A new process: empty lease registry and a new task registry/worker id."""

    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    monkeypatch.setattr(conversation_run_worker, "_registry", RunTaskRegistry())
    return registry


async def paired_then_lost(
    db: AsyncSession, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[ekit.Harness, uuid.UUID, uuid.UUID, PreparedRunRegistry, PreparedRunRegistry]:
    """Commit the marker pair + ``running`` and activate, then lose the process.

    From the restarted process this is indistinguishable from a crash after the
    commit: the lease exists nowhere and can never be rebuilt.
    Returns ``(harness, input_id, run_id, lost_registry, new_registry)``.
    """

    harness, input_id = await queue_harness(db, tmp_path, monkeypatch)
    run_id = await claim(db, harness)
    cfg = await ekit.entry_cfg(harness)
    target = conversation_run_worker._execution_target(  # noqa: SLF001
        run_id, harness.seeded.request.conversation_id, cfg
    )
    started = await start.start_owned_run(
        target,
        start.OwnedRunInputs(
            cfg,
            harness.seeded.user,
            PAYLOAD,
            conversation_run_queue_worker.execute_agent_stream_langgraph,
        ),
        worker_instance_id="worker-before-restart",
        started_at=0.0,
        transition=conversation_run_worker._transition_to_running,  # noqa: SLF001
    )
    assert started.started and started.executor is not None
    lost = get_prepared_run_registry()
    return harness, input_id, run_id, lost, restart(monkeypatch)
