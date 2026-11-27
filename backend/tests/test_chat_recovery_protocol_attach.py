from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.routers import conversation_agent_protocol_attach as attach
from tests.conftest import TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.fixture
async def owned_broker() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, EventBroker]]:
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = ConversationRun(
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=conversation.user_id,
            org_id=conversation.org_id,
            tenant_id=conversation.tenant_id,
            source="chat",
            status="running",
            is_active=True,
            owner_generation=2,
            owner_token=uuid.uuid4(),
            worker_instance_id="next-owner",
            last_event_id="durable-cursor",
        )
        db.add(run)
        await db.commit()
        run_id = run.id
    broker = broker_registry.get_or_create(str(run_id), conversation_id=str(conversation_id))
    broker.owner_generation = 1
    broker.publish_nowait(
        {
            "id": "buffered",
            "event": "message",
            "data": {
                "type": "event",
                "method": "messages",
                "seq": 1,
                "event_id": "buffered",
                "params": {"namespace": [], "data": {"chunk": "from-local"}},
            },
        }
    )
    try:
        yield conversation_id, run_id, broker
    finally:
        broker.close()


def _url(conversation_id: uuid.UUID) -> str:
    return f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/stream/events"


@pytest.mark.asyncio
async def test_protocol_attach_retries_with_durable_cursor_for_remote_owner(
    client: AsyncClient,
    owned_broker: tuple[uuid.UUID, uuid.UUID, EventBroker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, run_id, _ = owned_broker
    monkeypatch.setattr(attach, "_RUN_ATTACH_SETTLE_SECONDS", 0.01)
    response = await asyncio.wait_for(
        client.post(_url(conversation_id), json={"channels": ["messages"]}), timeout=2
    )
    assert response.status_code == 409
    assert response.json()["error"] == "RUN_ATTACH_RETRY"
    assert response.json()["last_event_id"] == "durable-cursor"
    assert response.json()["run_id"] == str(run_id)
    assert "from-local" not in response.text


@pytest.mark.asyncio
async def test_protocol_attach_rechecks_terminal_from_remote_while_waiting(
    client: AsyncClient, owned_broker: tuple[uuid.UUID, uuid.UUID, EventBroker]
) -> None:
    conversation_id, run_id, _ = owned_broker

    async def finish() -> None:
        await asyncio.sleep(0.05)
        async with TestSession() as db:
            run = await db.get(ConversationRun, run_id)
            assert run is not None
            run.status, run.is_active = "completed", False
            db.add(
                MessageEvent(
                    conversation_id=conversation_id,
                    assistant_msg_id=str(run_id),
                    events=[
                        stored_protocol_event(
                            run_id=str(run_id),
                            thread_id=str(conversation_id),
                            seq=1,
                            method="messages",
                            data={"chunk": "from-remote"},
                            event_id="durable-result",
                        )
                    ],
                    last_event_id="durable-result",
                    status="completed",
                )
            )
            await db.commit()

    response, _ = await asyncio.wait_for(
        asyncio.gather(
            client.post(_url(conversation_id), json={"channels": ["messages"]}), finish()
        ),
        timeout=3,
    )
    assert response.status_code == 200
    assert response.headers["x-resume-mode"] == "replay"
    assert "from-remote" in response.text
    assert "from-local" not in response.text


@pytest.mark.asyncio
async def test_live_protocol_attach_closes_after_silent_owner_change(
    client: AsyncClient,
    owned_broker: tuple[uuid.UUID, uuid.UUID, EventBroker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, run_id, broker = owned_broker
    broker.owner_generation = 2
    monkeypatch.setattr(attach, "_RUN_OWNER_POLL_SECONDS", 0.02)

    async def take_over() -> None:
        await broker.wait_until_subscribed()
        async with TestSession() as db:
            run = await db.get(ConversationRun, run_id)
            assert run is not None
            run.owner_generation += 1
            run.owner_token = uuid.uuid4()
            await db.commit()

    response, _ = await asyncio.wait_for(
        asyncio.gather(
            client.post(_url(conversation_id), json={"channels": ["messages"]}), take_over()
        ),
        timeout=3,
    )
    assert response.status_code == 200
    assert response.headers["x-resume-mode"] == "live"
    assert response.text.count("from-local") == 1
    assert not broker.is_closed
