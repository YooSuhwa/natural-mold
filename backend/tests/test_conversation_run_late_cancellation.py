from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import (
    conversation_run_queue_service,
    conversation_run_queue_worker,
    conversation_run_service,
    conversation_run_worker,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration import _seed as seed_helpers


@pytest.mark.asyncio
async def test_owner_finalizer_honors_remote_cancel_after_executor_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real owner task and a queued steer successor.
    session_factory = TestSession
    postgres_engine: AsyncEngine | None = None
    raw_postgres_url = os.environ.get("LATE_CANCEL_DATABASE_URL")
    if raw_postgres_url is not None:
        parsed_url = make_url(raw_postgres_url)
        if not (parsed_url.database or "").startswith("moldy_pg_lane_late_cancel_"):
            pytest.fail("late-cancel PostgreSQL proof requires a disposable lane database")
        postgres_engine = create_async_engine(parsed_url.set(drivername="postgresql+asyncpg"))
        session_factory = async_sessionmaker(
            postgres_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(seed_helpers, "TestSession", session_factory)
        monkeypatch.setattr(conversation_run_worker, "async_session", session_factory)
        monkeypatch.setattr(conversation_run_queue_worker, "async_session", session_factory)
        monkeypatch.setattr(
            conversation_run_worker.stream_service,
            "async_session",
            session_factory,
        )

    conversation_id = await seed_helpers.seed_conversation_with_agent()
    async with session_factory() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        predecessor = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="natural completion at cancellation boundary",
        )
        successor = await conversation_run_queue_service.enqueue_input(
            db,
            conversation_id=conversation_id,
            user_id=TEST_USER_ID,
            client_request_id="late-cancel-successor",
            source="chat",
            input_payload={"messages": [{"role": "user", "content": "correct it"}]},
            attachment_ids=[],
            checkpoint_id=None,
            priority=100,
        )
        await db.commit()
        predecessor_id = predecessor.id
        successor_id = successor.id

    heartbeat_started = anyio.Event()
    executor_exhausted = anyio.Event()
    owner_finalizer_released = anyio.Event()
    launched_input_ids: list[object] = []

    async def exhausted_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        await heartbeat_started.wait()
        executor_exhausted.set()
        if False:
            yield ""

    async def hold_owner_after_executor_exhaustion(
        _run_id: object,
        _registry: object,
    ) -> None:
        heartbeat_started.set()
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            await owner_finalizer_released.wait()

    async def prepared_metrics(**kwargs: Any) -> RunMetricsAccumulator:
        return RunMetricsAccumulator(started_at=kwargs["started_at"])

    async def record_launch(input_id: object) -> ConversationRun | None:
        launched_input_ids.append(input_id)
        return None

    monkeypatch.setattr(
        conversation_run_worker,
        "_heartbeat_until_terminal",
        hold_owner_after_executor_exhaustion,
    )
    monkeypatch.setattr(conversation_run_worker, "_prepare_run_metrics", prepared_metrics)
    monkeypatch.setattr(conversation_run_queue_worker, "launch_claimed_input", record_launch)

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="actual-owner")
    ctx = await conversation_run_worker.start_conversation_run(
        run_id=predecessor_id,
        conversation_id=conversation_id,
        cfg=cast(
            AgentConfig,
            SimpleNamespace(
                secret_values=set(),
                agent_id=None,
                model_name="boundary-model",
                checkpoint_id=None,
            ),
        ),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "finish"}]},
        moldy_source="chat",
        executor_fn=exhausted_executor,
        registry=registry,
    )
    with anyio.fail_after(2):
        await executor_exhausted.wait()

    # When a remote session durably cancels after exhaustion but before owner finalization.
    async with session_factory() as remote_db:
        durable = await remote_db.get(ConversationRun, predecessor_id, with_for_update=True)
        assert durable is not None
        assert durable.status == "running"
        assert durable.worker_instance_id == "actual-owner"
        await conversation_run_service.request_cancel_run(remote_db, durable, reason="steer")
        await remote_db.commit()

    async with session_factory() as negative_control_db:
        durable = await negative_control_db.get(
            ConversationRun,
            predecessor_id,
            with_for_update=True,
        )
        assert durable is not None and durable.status == "canceling"
        with pytest.raises(ValueError, match="owning worker"):
            await conversation_run_service.transition_run(
                negative_control_db,
                durable,
                "canceled",
                cancellation_ack_worker_id="foreign-worker",
            )
        await negative_control_db.rollback()

    owner_finalizer_released.set()
    owner_task = registry.get(predecessor_id)
    assert owner_task is not None
    with anyio.fail_after(2):
        await owner_task

    # Then raw durable state, projections, and dispatch all reflect owner cancellation.
    async with session_factory() as verification_db:
        durable = await verification_db.get(ConversationRun, predecessor_id)
        metrics = await verification_db.get(ConversationRunMetrics, predecessor_id)
        successor = await verification_db.get(ConversationRunInput, successor_id)
        output = await verification_db.scalar(
            select(MessageEvent).where(MessageEvent.assistant_msg_id == str(predecessor_id))
        )
        output_chunks = list(
            (
                await verification_db.scalars(
                    select(MessageEventChunk)
                    .where(MessageEventChunk.assistant_msg_id == str(predecessor_id))
                    .order_by(MessageEventChunk.seq_start)
                )
            ).all()
        )
        audit_actions = list(
            (
                await verification_db.scalars(
                    select(AuditEvent.action)
                    .where(AuditEvent.run_id == str(predecessor_id))
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            ).all()
        )
        claimed_successors = list(
            (
                await verification_db.scalars(
                    select(ConversationRunInput).where(
                        ConversationRunInput.conversation_id == conversation_id,
                        ConversationRunInput.status == "claimed",
                    )
                )
            ).all()
        )

    assert durable is not None
    assert durable.status == "canceled"
    assert durable.cancel_reason == "steer"
    assert durable.worker_instance_id == "actual-owner"
    assert durable.cancellation_acknowledged_at is not None
    assert durable.last_event_id == f"{predecessor_id}-canceled"
    assert metrics is not None and metrics.terminal_state == "canceled"
    assert audit_actions == ["conversation.run_start", "conversation.run_canceled"]
    assert output is not None
    assert output.status == "completed"
    assert output.last_event_id == f"{predecessor_id}-canceled"
    assert [event for chunk in output_chunks for event in chunk.events] == [
        {
            "id": f"{predecessor_id}-canceled",
            "event": "message_end",
            "data": {"usage": {}, "content": "", "status": "canceled"},
        }
    ]
    assert ctx.trace_sink[-1]["event"] == "message_end"
    assert ctx.trace_sink[-1]["data"]["status"] == "canceled"
    assert successor is not None and successor.status == "claimed" and successor.run_id is not None
    assert [item.id for item in claimed_successors] == [successor_id]
    assert launched_input_ids == [successor_id]
    if postgres_engine is not None:
        await postgres_engine.dispose()
