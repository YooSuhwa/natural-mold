from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Mapping
from typing import cast

import anyio
import pytest
from sqlalchemy import select

from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.langgraph_lifecycle_events import lifecycle_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import PersistCallback
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import conversation_run_queue_worker, conversation_run_service
from app.services import conversation_run_worker as worker
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


def _terminal_status(event: Mapping[str, object]) -> str | None:
    terminal_statuses = {"completed", "canceled", "failed", "interrupted", "stale"}
    if event.get("event") == "message_end":
        data = event.get("data")
        status = data.get("status") if isinstance(data, Mapping) else None
        return status if status in terminal_statuses else None
    data = event.get("data")
    if event.get("method") == "lifecycle" and isinstance(data, Mapping):
        status = data.get("event")
        return status if status in terminal_statuses else None
    if not isinstance(data, Mapping) or data.get("method") != "lifecycle":
        return None
    params = data.get("params")
    lifecycle = params.get("data") if isinstance(params, Mapping) else None
    status = lifecycle.get("event") if isinstance(lifecycle, Mapping) else None
    return status if status in terminal_statuses else None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_cancel", "expected_status"),
    [(False, "completed"), (True, "canceled")],
    ids=["normal-completion-control", "late-owner-cancel"],
)
async def test_owner_decision_precedes_protocol_terminal_delivery(
    monkeypatch: pytest.MonkeyPatch,
    remote_cancel: bool,
    expected_status: str,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="delivery boundary",
        )
        await db.commit()
        run_id = run.id

    heartbeat_started = anyio.Event()
    executor_exhausted = anyio.Event()
    finalizer_released = anyio.Event()

    async def hold_finalizer(_run_id: uuid.UUID, _registry: worker.RunTaskRegistry) -> None:
        heartbeat_started.set()
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            await finalizer_released.wait()

    async def prepared_metrics(**kwargs: object) -> RunMetricsAccumulator:
        started_at = kwargs["started_at"]
        assert isinstance(started_at, float)
        return RunMetricsAccumulator(started_at=started_at)

    async def no_successor(_conversation_id: uuid.UUID) -> None:
        return None

    async def protocol_executor(
        _cfg: AgentConfig,
        _input_payload: object,
        **kwargs: object,
    ) -> AsyncGenerator[str, None]:
        broker = kwargs["broker"]
        trace_sink = kwargs["trace_sink"]
        persist_callback = kwargs["persist_callback"]
        run_id_value = kwargs["run_id"]
        assert callable(getattr(broker, "wait_until_subscribed", None))
        assert isinstance(trace_sink, list)
        assert callable(persist_callback)
        assert isinstance(run_id_value, str)
        await heartbeat_started.wait()
        delivery_broker = cast(EventBroker, broker)
        await delivery_broker.wait_until_subscribed()
        delivery = ProtocolEventDelivery(
            run_id=run_id_value,
            trace_sink=trace_sink,
            broker=delivery_broker,
            persist_callback=cast(PersistCallback, persist_callback),
        )
        yield await delivery.emit(
            lifecycle_protocol_event(
                run_id=run_id_value,
                thread_id=str(conversation_id),
                seq=0,
                event="running",
            )
        )
        yield await delivery.emit(
            lifecycle_protocol_event(
                run_id=run_id_value,
                thread_id=str(conversation_id),
                seq=1,
                event="completed",
            )
        )
        await delivery.close()
        executor_exhausted.set()

    monkeypatch.setattr(worker, "_heartbeat_until_terminal", hold_finalizer)
    monkeypatch.setattr(worker, "_prepare_run_metrics", prepared_metrics)
    monkeypatch.setattr(
        conversation_run_queue_worker,
        "dispatch_next_for_conversation",
        no_successor,
    )

    registry = worker.RunTaskRegistry(worker_instance_id="delivery-owner")
    ctx = await worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=AgentConfig(
            provider="test",
            model_name="delivery-model",
            api_key=None,
            base_url=None,
            system_prompt="",
            tools_config=[],
            thread_id=str(conversation_id),
        ),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "finish"}]},
        moldy_source="chat",
        executor_fn=protocol_executor,
        registry=registry,
    )

    live_events: list[BrokeredEvent] = []

    async def subscribe() -> None:
        async for event in ctx.broker.subscribe():
            live_events.append(event)

    subscriber = asyncio.create_task(subscribe())
    with anyio.fail_after(2):
        await executor_exhausted.wait()

    if remote_cancel:
        async with TestSession() as remote_db:
            durable = await remote_db.get(ConversationRun, run_id, with_for_update=True)
            assert durable is not None
            await conversation_run_service.request_cancel_run(remote_db, durable, reason="steer")
            await remote_db.commit()

    finalizer_released.set()
    owner_task = registry.get(run_id)
    assert owner_task is not None
    with anyio.fail_after(2):
        await owner_task
        await subscriber

    async with TestSession() as db:
        durable = await db.get(ConversationRun, run_id)
        trace = await db.scalar(
            select(MessageEvent).where(MessageEvent.assistant_msg_id == str(run_id))
        )
        chunks = list(
            (
                await db.scalars(
                    select(MessageEventChunk)
                    .where(MessageEventChunk.assistant_msg_id == str(run_id))
                    .order_by(MessageEventChunk.seq_start)
                )
            ).all()
        )

    live_terminals = [status for event in live_events if (status := _terminal_status(event))]
    persisted_events = [event for chunk in chunks for event in chunk.events]
    persisted_terminals = [
        status for event in persisted_events if (status := _terminal_status(event))
    ]
    assert durable is not None and durable.status == expected_status
    assert trace is not None and trace.status == "completed"
    assert live_terminals == [expected_status]
    assert persisted_terminals == [expected_status]
    assert ctx.broker.is_closed
