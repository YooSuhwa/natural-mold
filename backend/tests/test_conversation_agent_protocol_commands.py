from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_protocol_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="protocol@test.dev", name="Protocol User")
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


@pytest.mark.asyncio
async def test_input_respond_command_starts_langgraph_resume_run(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id="intr-1",
    )
    db.add(parent_run)
    await db.commit()
    started = {}

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-1",
            "method": "input.respond",
            "params": {
                "namespace": [],
                "interrupt_id": "intr-1",
                "response": {"decisions": [{"type": "approve"}]},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    payload = response.json()
    assert payload["type"] == "success"
    assert payload["id"] == "resume-1"
    assert payload["result"]["thread_id"] == str(conversation.id)
    assert uuid.UUID(payload["result"]["run_id"])
    assert response.headers["x-run-id"] == payload["result"]["run_id"]
    assert started["run_id"] == uuid.UUID(payload["result"]["run_id"])
    assert started["conversation_id"] == conversation.id
    assert started["input_payload"] == {"decisions": [{"type": "approve"}]}
    assert started["moldy_source"] == "resume"
    assert started["executor_fn"].__name__ == "resume_agent_stream_langgraph"


@pytest.mark.asyncio
async def test_input_respond_command_preserves_batched_interrupt_responses(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    db.add(
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-a",
        )
    )
    await db.commit()
    started = {}

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-many",
            "method": "input.respond",
            "params": {
                "responses": [
                    {
                        "namespace": [],
                        "interrupt_id": "intr-a",
                        "response": {"decisions": [{"type": "approve"}]},
                    },
                    {
                        "namespace": [],
                        "interrupt_id": "intr-b",
                        "response": {"decisions": [{"type": "reject", "message": "no"}]},
                    },
                ]
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["type"] == "success"
    assert started["input_payload"] == {
        "intr-a": {"decisions": [{"type": "approve"}]},
        "intr-b": {"decisions": [{"type": "reject", "message": "no"}]},
    }
