from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import conversation_run_service
from app.services.conversation_run_worker import RunTaskRegistry
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


@pytest.mark.asyncio
async def test_workerless_ack_requires_null_owner(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="workerless",
    )
    await conversation_run_service.request_cancel_run(db, run)
    await conversation_run_service.transition_run(
        db, run, "canceled", allow_workerless_cancellation_ack=True
    )
    assert run.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_workerless_terminal_finalization_writes_one_event_audit_and_metrics(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="terminal-once",
    )
    await conversation_run_service.request_cancel_run(db, run)
    await db.commit()
    run_id = run.id

    executor_called = False

    async def never_dispatch(*_args: object, **_kwargs: object):
        nonlocal executor_called
        executor_called = True
        if False:
            yield ""

    registry = RunTaskRegistry(worker_instance_id="terminal-owner")
    from app.services import conversation_run_worker

    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=await run_fixture_config(run_id, TestSession),
        user=CurrentUser(id=root.user_id, email="owner@task6.test", name="Task 6 Owner"),
        input_payload={"content": "never dispatched"},
        moldy_source="chat",
        executor_fn=never_dispatch,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert executor_called is False
    async with TestSession() as verify_db:
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(ConversationRunMetrics)
                .where(ConversationRunMetrics.run_id == run_id)
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.run_id == str(run_id),
                    AuditEvent.action == "conversation.run_canceled",
                )
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(MessageEvent)
                .where(MessageEvent.assistant_msg_id == str(run_id))
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(MessageEventChunk)
                .where(MessageEventChunk.assistant_msg_id == str(run_id))
            )
            == 1
        )
