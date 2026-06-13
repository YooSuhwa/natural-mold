from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_protocol_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="protocol-state@test.dev", name="Protocol User")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Protocol State Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Protocol State Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def _approval_payload() -> dict[str, object]:
    return {
        "action_requests": [{"name": "send_email", "args": {"to": "user@example.com"}}],
        "review_configs": [
            {"action_name": "send_email", "allowed_decisions": ["approve", "reject"]}
        ],
    }


@pytest.mark.asyncio
async def test_thread_state_hydrates_pending_input_requested_interrupt(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    run_id = uuid.uuid4()
    payload = _approval_payload()
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-1",
        )
    )
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=[
                stored_protocol_event(
                    run_id=str(run_id),
                    thread_id=str(conversation.id),
                    seq=4,
                    method="input.requested",
                    namespace=["tools:call-1"],
                    data={"interrupt_id": "intr-1", "payload": payload},
                    event_id="input-evt-1",
                )
            ],
            last_event_id="input-evt-1",
            status="completed",
        )
    )
    await db.commit()

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    interrupt = {"id": "intr-1", "value": payload, "ns": ["tools:call-1"]}
    assert state_response.json()["tasks"] == [
        {"id": str(run_id), "name": "interrupted", "interrupts": [interrupt]}
    ]

    assert compat_response.status_code == 200
    assert compat_response.json()["interrupts"] == [interrupt]


@pytest.mark.asyncio
async def test_thread_state_omits_interrupt_after_resume_child_exists(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    parent_run = ConversationRun(
        id=parent_run_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id="intr-1",
    )
    db.add(parent_run)
    db.add(
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            parent_run_id=parent_run.id,
            source="resume",
            status="completed",
            is_active=False,
            interrupt_id="intr-1",
        )
    )
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(parent_run_id),
            events=[
                stored_protocol_event(
                    run_id=str(parent_run_id),
                    thread_id=str(conversation.id),
                    seq=4,
                    method="input.requested",
                    data={"interrupt_id": "intr-1", "payload": _approval_payload()},
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    assert state_response.json()["tasks"] == []
    assert compat_response.status_code == 200
    assert compat_response.json()["interrupts"] == []
