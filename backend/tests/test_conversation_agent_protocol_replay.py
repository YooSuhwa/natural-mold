from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="protocol-replay@test.dev", name="Replay")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Replay Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Replay Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


@pytest.mark.asyncio
async def test_protocol_replay_resumes_after_last_event_id_without_duplicates(
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
                    data={"chunk": "already-seen"},
                    event_id="upstream-1",
                ),
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=2,
                    method="messages",
                    data={"chunk": "next-token"},
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
        json={"channels": ["messages"]},
        headers={"Last-Event-ID": "upstream-1"},
    )

    assert response.status_code == 200
    assert response.headers["x-resume-mode"] == "replay"
    assert "next-token" in response.text
    assert "already-seen" not in response.text


@pytest.mark.asyncio
async def test_protocol_stream_marks_stale_active_run_and_emits_custom_stale_event(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4()
    stale_time = utc_now_naive().replace(year=2000)
    run = ConversationRun(
        id=run_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="running",
        is_active=True,
        heartbeat_at=stale_time,
        started_at=stale_time,
        created_at=stale_time,
        last_event_id="upstream-1",
    )
    db.add(run)
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=[
                stored_protocol_event(
                    run_id=str(run_id),
                    thread_id=str(conversation.id),
                    seq=1,
                    method="messages",
                    data={"chunk": "partial"},
                    event_id="upstream-1",
                )
            ],
            last_event_id="upstream-1",
            status="streaming",
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["messages", "custom"]},
    )

    assert response.status_code == 200
    assert response.headers["x-resume-mode"] == "stale"
    assert "partial" in response.text
    assert '"method":"custom:stale"' in response.text
    assert "run_worker_lost" in response.text
    await db.refresh(run)
    assert run.status == "stale"
    assert run.is_active is False
