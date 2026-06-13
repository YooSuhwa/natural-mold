from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.main import create_app
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
) -> Conversation:
    user = await db.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=f"{user_id.hex[:8]}@test.dev", name="Test")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Protocol Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Protocol Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def test_agent_protocol_routes_registered_from_conversation_facade() -> None:
    from fastapi.routing import APIRoute

    expected = {
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/commands"),
        ("GET", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state"),
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state"),
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/history"),
        (
            "POST",
            "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/stream/events",
        ),
        ("GET", "/api/conversations/{conversation_id}/langgraph/state"),
    }
    actual = {
        (method, route.path)
        for route in create_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert expected <= actual


@pytest.mark.asyncio
async def test_thread_state_requires_authentication(raw_client: AsyncClient) -> None:
    conversation_id = uuid.uuid4()

    response = await raw_client.get(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/state"
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_thread_state_hides_conversations_owned_by_another_user(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db, user_id=uuid.uuid4())

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_start_command_defaults_multitask_strategy_to_reject(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "cmd-1",
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        },
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    payload = response.json()
    assert payload["type"] == "success"
    assert payload["id"] == "cmd-1"
    assert payload["result"]["multitask_strategy"] == "reject"
    assert payload["result"]["thread_id"] == str(conversation.id)


@pytest.mark.asyncio
async def test_command_rejects_unknown_methods_with_agent_protocol_error(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={"id": "cmd-2", "method": "run.teleport", "params": {}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": "cmd-2",
        "error": {
            "code": "UNSUPPORTED_COMMAND",
            "message": "Unsupported command method: run.teleport",
        },
    }


@pytest.mark.asyncio
async def test_thread_state_and_history_use_sdk_compatible_shapes(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    state_url = f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"

    state_response = await client.get(state_url)
    update_response = await client.post(
        state_url,
        json={"values": {"todos": [{"id": "t1", "content": "plan"}]}},
    )
    history_response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/history",
        json={"limit": 1},
    )

    assert state_response.status_code == 200
    state = state_response.json()
    assert state["values"] == {"messages": []}
    assert state["next"] == []
    assert state["tasks"] == []
    assert state["metadata"]["conversation_id"] == str(conversation.id)

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["values"]["messages"] == []
    assert updated["values"]["todos"] == [{"id": "t1", "content": "plan"}]

    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history) == 1
    assert history[0]["values"] == {"messages": []}


@pytest.mark.asyncio
async def test_protocol_stream_replays_stored_canonical_events_with_filters(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=run_id,
            events=[
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=1,
                    method="messages",
                    namespace=["agent"],
                    data={"chunk": "hello"},
                    event_id="upstream-1",
                ),
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=2,
                    method="tools",
                    namespace=["agent"],
                    data={"event": "tool-started"},
                    event_id="upstream-2",
                ),
            ],
            last_event_id="upstream-2",
            status="completed",
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["messages"], "namespaces": [["agent"]], "depth": 0},
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    assert response.headers["x-resume-mode"] == "replay"
    assert "event: message" in response.text
    assert '"method":"messages"' in response.text
    assert "hello" in response.text
    assert "tool-started" not in response.text


@pytest.mark.asyncio
async def test_protocol_stream_filters_custom_named_channels(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=run_id,
            events=[
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=1,
                    method="custom",
                    data={"name": "artifact", "payload": {"path": "report.md"}},
                    event_id="custom-1",
                ),
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=2,
                    method="custom",
                    data={"name": "memory", "payload": {"key": "profile"}},
                    event_id="custom-2",
                ),
            ],
            last_event_id="custom-2",
            status="completed",
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["custom:artifact"]},
    )

    assert response.status_code == 200
    assert "report.md" in response.text
    assert "profile" not in response.text
