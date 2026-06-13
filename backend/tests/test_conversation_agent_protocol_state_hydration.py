from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID


class _FakeCheckpointer:
    def __init__(self, checkpoints: list[_CheckpointSlim]) -> None:
        self._checkpoints = checkpoints

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for checkpoint in self._checkpoints:
            yield type(
                "CheckpointTuple",
                (),
                {
                    "config": {"configurable": {"checkpoint_id": checkpoint.checkpoint_id}},
                    "parent_config": (
                        {"configurable": {"checkpoint_id": checkpoint.parent_checkpoint_id}}
                        if checkpoint.parent_checkpoint_id
                        else None
                    ),
                    "checkpoint": {"channel_values": {"messages": checkpoint.messages}},
                },
            )()


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
async def test_thread_state_hydrates_pending_tasks_interrupts(
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
            interrupt_id="intr-task",
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
                    seq=5,
                    method="tasks",
                    namespace=["agent"],
                    data={
                        "id": "task-1",
                        "name": "tools",
                        "interrupts": [
                            {"id": "intr-task", "value": payload, "ns": ["tools:call-1"]}
                        ],
                    },
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    assert response.json()["tasks"] == [
        {
            "id": str(run_id),
            "name": "interrupted",
            "interrupts": [{"id": "intr-task", "value": payload, "ns": ["tools:call-1"]}],
        }
    ]


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


@pytest.mark.asyncio
async def test_thread_state_exposes_checkpoint_mapping_for_messages(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id=None,
        messages=[HumanMessage(id="user-raw-1", content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant",
        parent_checkpoint_id="ck-user",
        messages=[
            HumanMessage(id="user-raw-1", content="hello"),
            AIMessage(id="assistant-raw-1", content="hi"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_runtime.get_checkpointer",
        lambda: _FakeCheckpointer([leaf, parent]),
    )

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    state = state_response.json()
    assert state["metadata"]["checkpoint_by_message_id"] == {
        "user-raw-1": "ck-user",
        "assistant-raw-1": "ck-assistant",
    }
    messages = state["values"]["messages"]
    assert messages[0]["additional_kwargs"]["metadata"]["checkpoint_id"] == "ck-user"
    assert messages[1]["additional_kwargs"]["metadata"]["checkpoint_id"] == "ck-assistant"

    assert compat_response.status_code == 200
    assert compat_response.json()["checkpoint_by_message_id"] == {
        "user-raw-1": "ck-user",
        "assistant-raw-1": "ck-assistant",
    }
