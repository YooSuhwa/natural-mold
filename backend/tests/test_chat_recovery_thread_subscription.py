from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.routers import conversation_agent_protocol_attach as attach
from app.routers import conversation_agent_protocol_thread_stream as thread_stream
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


async def _seed_run() -> tuple[uuid.UUID, uuid.UUID]:
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        run = ConversationRun(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            agent_id=conv.agent_id,
            user_id=conv.user_id,
            org_id=conv.org_id,
            tenant_id=conv.tenant_id,
            source="chat",
            status="running",
            is_active=True,
            owner_generation=1,
            owner_token=uuid.uuid4(),
            worker_instance_id="first",
        )
        db.add(run)
        await db.commit()
        return conversation_id, run.id


def _publish(broker: EventBroker, event_id: str) -> None:
    broker.publish_nowait(
        {
            "id": event_id,
            "event": "message",
            "data": {
                "type": "event",
                "method": "lifecycle",
                "params": {"namespace": [], "data": {"event": "running"}},
                "seq": 1,
                "event_id": event_id,
            },
        }
    )


def _subscribe(conversation_id: uuid.UUID) -> AsyncGenerator[str, None]:
    async def connected() -> bool:
        return False

    return thread_stream.protocol_thread_stream_generator(
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        thread_id=str(conversation_id),
        params={"channels": ["lifecycle"]},
        after_id=None,
        is_disconnected=connected,
    )


@pytest.mark.asyncio
async def test_subscription_follows_remote_completion_while_old_broker_remains_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attach, "_RUN_OWNER_POLL_SECONDS", 0.04)
    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_OWNER_POLL_SECONDS", 0.04)
    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_POLL_SECONDS", 0.01)
    conversation_id, run_id = await _seed_run()
    broker = broker_registry.get_or_create(str(run_id), conversation_id=str(conversation_id))
    broker.owner_generation = 1
    _publish(broker, "owner-one")
    stream = _subscribe(conversation_id)
    try:
        first = await asyncio.wait_for(anext(stream), 2)
        assert first.startswith("id: owner-one\n")
        async with TestSession() as db:
            run = await db.get(ConversationRun, run_id)
            assert run is not None
            run.owner_generation = 2
            run.owner_token = uuid.uuid4()
            run.worker_instance_id = "replacement"
            run.is_active = False
            run.status = "completed"
            db.add(
                MessageEvent(
                    conversation_id=conversation_id,
                    assistant_msg_id=str(run_id),
                    events=[
                        dict(
                            stored_protocol_event(
                                run_id=str(run_id),
                                thread_id=str(conversation_id),
                                seq=i,
                                method="lifecycle",
                                data={"event": state},
                                event_id=event_id,
                            )
                        )
                        for i, (state, event_id) in enumerate(
                            [("running", "owner-one"), ("completed", "owner-two")], start=1
                        )
                    ],
                    status="completed",
                    last_event_id="owner-two",
                )
            )
            await db.commit()
        second = await asyncio.wait_for(anext(stream), 2)
        assert second.startswith("id: owner-two\n")
        assert broker.is_closed is False
    finally:
        await stream.aclose()
        broker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_generation", [0, 2])
async def test_subscribe_rejects_open_broker_of_wrong_generation(
    wrong_generation: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_HEARTBEAT_SECONDS", 0.03)
    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_POLL_SECONDS", 0.01)
    conversation_id, run_id = await _seed_run()
    broker = broker_registry.get_or_create(str(run_id), conversation_id=str(conversation_id))
    broker.owner_generation = wrong_generation
    _publish(broker, "wrong-generation")
    stream = _subscribe(conversation_id)
    try:
        assert await asyncio.wait_for(anext(stream), 2) == ": heartbeat\n\n"
    finally:
        await stream.aclose()
        broker.close()
