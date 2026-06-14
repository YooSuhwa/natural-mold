from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import stored_custom_protocol_event, stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.services import trace_storage
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
async def test_protocol_replay_delivers_named_custom_event_after_numeric_cursor() -> None:
    run_id = "run-with-monotonic-seq"
    thread_id = "thread-with-monotonic-seq"
    first_message = stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=1,
        method="messages",
        data={"chunk": "first"},
    )
    usage_event_id = f"{first_message['id']}:usage"
    usage_event = stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=2,
        name="usage",
        payload={
            "run_id": run_id,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        },
        event_id=usage_event_id,
        id=usage_event_id,
    )
    second_message = stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=3,
        method="messages",
        data={"chunk": "second"},
    )

    chunks = [
        chunk
        async for chunk in protocol_replay_generator(
            [first_message, usage_event, second_message],
            {"channels": ["custom:usage"]},
            after_id="1",
        )
    ]

    body = "".join(chunks)
    assert '"method":"custom"' in body
    assert '"name":"usage"' in body
    assert '"prompt_tokens":1' in body
    assert "first" not in body


@pytest.mark.asyncio
async def test_protocol_replay_loads_append_only_chunks(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    usage_event = stored_custom_protocol_event(
        run_id=run_id,
        thread_id=str(conversation.id),
        seq=12,
        name="usage",
        payload={
            "run_id": run_id,
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        },
        event_id="usage-1",
    )
    await trace_storage.append_events(
        db,
        conversation_id=conversation.id,
        assistant_msg_id=run_id,
        events_chunk=[dict(usage_event)],
        status="completed",
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["custom:usage"]},
    )

    assert response.status_code == 200
    assert response.headers["x-resume-mode"] == "replay"
    assert '"method":"custom"' in response.text
    assert '"name":"usage"' in response.text
    assert '"prompt_tokens":120' in response.text


@pytest.mark.asyncio
async def test_protocol_replay_projects_run_local_sequences_to_thread_sequence(
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    first_run_id = uuid.uuid4().hex
    second_run_id = uuid.uuid4().hex
    db.add_all(
        [
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=first_run_id,
                events=[
                    stored_protocol_event(
                        run_id=first_run_id,
                        thread_id=str(conversation.id),
                        seq=1,
                        method="messages",
                        data={"chunk": "first-run-1"},
                    ),
                    stored_protocol_event(
                        run_id=first_run_id,
                        thread_id=str(conversation.id),
                        seq=2,
                        method="messages",
                        data={"chunk": "first-run-2"},
                    ),
                ],
                status="completed",
            ),
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=second_run_id,
                events=[
                    stored_protocol_event(
                        run_id=second_run_id,
                        thread_id=str(conversation.id),
                        seq=1,
                        method="messages",
                        data={"chunk": "second-run-1"},
                    )
                ],
                status="completed",
            ),
        ]
    )
    await db.commit()

    events = await load_protocol_events(db, conversation.id)
    chunks = [
        chunk
        async for chunk in protocol_replay_generator(
            events,
            {"channels": ["messages"], "since": 2},
            after_id=None,
        )
    ]

    body = "".join(chunks)
    assert "second-run-1" in body
    assert "first-run-1" not in body
    assert "first-run-2" not in body


@pytest.mark.asyncio
async def test_protocol_replay_projects_legacy_terminal_events(
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=run_id,
            events=[
                {
                    "id": f"{run_id}-1",
                    "event": event_names.MESSAGE_END,
                    "data": {"content": "", "usage": {}, "status": "canceled"},
                },
                {
                    "id": f"{run_id}-stale",
                    "event": event_names.STALE,
                    "data": {
                        "reason": "run_worker_lost",
                        "run_id": run_id,
                        "last_event_id": f"{run_id}-1",
                    },
                },
            ],
            last_event_id=f"{run_id}-stale",
            status="completed",
        )
    )
    await db.commit()

    events = await load_protocol_events(db, conversation.id)
    chunks = [
        chunk
        async for chunk in protocol_replay_generator(
            events,
            {"channels": ["lifecycle", "custom:stale"]},
            after_id=None,
        )
    ]
    body = "".join(chunks)

    assert '"method":"lifecycle"' in body
    assert '"status":"canceled"' in body
    assert '"status":"error"' in body
    assert '"method":"custom"' in body
    assert '"name":"stale"' in body
    assert "run_worker_lost" in body


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
    assert '"method":"custom"' in response.text
    assert '"name":"stale"' in response.text
    assert "run_worker_lost" in response.text
    await db.refresh(run)
    assert run.status == "stale"
    assert run.is_active is False
