from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service
from tests.conftest import TEST_USER_ID


async def _seed_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    email: str = "run-links@test.local",
) -> tuple[Agent, Conversation]:
    user = User(id=user_id, email=email, name="Run Links User")
    model = Model(
        provider="openai",
        model_name=f"links-model-{user_id.hex}",
        display_name="Links",
    )
    db.add_all([user, model])
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Run Links Agent",
        system_prompt="help",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="Run links")
    db.add(conversation)
    await db.flush()
    return agent, conversation


async def _create_run(
    db: AsyncSession,
    *,
    agent: Agent,
    conversation: Conversation,
) -> ConversationRun:
    return await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="link",
    )


@pytest.mark.asyncio
async def test_links_return_unambiguous_messages_and_omit_distinct_run_collisions(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_conversation(db)
    older = await _create_run(db, agent=agent, conversation=conversation)
    await conversation_run_service.transition_run(db, older, "running")
    await conversation_run_service.transition_run(db, older, "completed")
    newer = await _create_run(db, agent=agent, conversation=conversation)
    shared_message_id = uuid.uuid4()
    older_only_message_id = uuid.uuid4()
    missing_message_id = uuid.uuid4()
    db.add_all(
        [
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=str(older.id),
                linked_message_ids=[str(shared_message_id), str(older_only_message_id)],
                events=[],
            ),
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=str(newer.id),
                linked_message_ids=[str(shared_message_id)],
                events=[],
            ),
        ]
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params=[
            ("message_id", str(older_only_message_id)),
            ("message_id", str(shared_message_id)),
            ("message_id", str(shared_message_id)),
            ("message_id", str(missing_message_id)),
        ],
    )

    assert response.status_code == 200
    assert response.json() == [
        {"message_id": str(older_only_message_id), "run_id": str(older.id)},
    ]


@pytest.mark.asyncio
async def test_link_query_is_request_bounded_and_projects_no_trace_bodies(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_conversation(db)
    now = utc_now_naive()
    runs = [
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="chat",
            status="completed",
            is_active=False,
            created_at=now - timedelta(seconds=index),
        )
        for index in range(64)
    ]
    db.add_all(runs)
    await db.flush()
    message_id = uuid.uuid4()
    db.add_all(
        [
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=str(run.id),
                linked_message_ids=[str(message_id if index == 0 else uuid.uuid4())],
                events=[{"secret": "must-not-be-projected"}],
            )
            for index, run in enumerate(runs)
        ]
    )
    await db.commit()
    statements: list[tuple[str, int]] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        lowered = statement.lower()
        if "message_events" in lowered or "from conversation_runs" in lowered:
            statements.append((lowered, len(_parameters)))

    sync_engine = db.get_bind().engine
    event.listen(sync_engine, "before_cursor_execute", capture_statement)
    try:
        response = await client.get(
            f"/api/conversations/{conversation.id}/run-message-links",
            params={"message_id": str(message_id)},
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert response.json() == [{"message_id": str(message_id), "run_id": str(runs[0].id)}]
    assert len(statements) == 1
    assert statements[0][1] <= 10
    assert "message_events.events" not in statements[0][0]
    assert "message_event_chunks" not in statements[0][0]
    assert "message_events.linked_message_ids" in statements[0][0]


@pytest.mark.asyncio
async def test_link_endpoint_hides_foreign_owner_conversation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    other_id = uuid.UUID("00000000-0000-0000-0000-000000000098")
    _agent, conversation = await _seed_conversation(
        db,
        user_id=other_id,
        email="foreign-links@test.local",
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params={"message_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        None,
        {"message_id": ""},
        {"message_id": "x" * 513},
        [("message_id", str(uuid.uuid4()))] * 51,
    ],
)
async def test_link_endpoint_rejects_missing_malformed_or_oversized_batch(
    client: AsyncClient,
    db: AsyncSession,
    params,
) -> None:
    _agent, conversation = await _seed_conversation(db)
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params=params,
    )

    assert response.status_code == 422
