from __future__ import annotations

import uuid

import pytest

from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.anyio


async def test_ui_conversation_lists_exclude_api_source(db):
    from app.models.agent import Agent
    from app.models.conversation import Conversation
    from app.models.model import Model
    from app.models.user import User
    from app.services import chat_service

    user = User(
        id=TEST_USER_ID,
        email="owner@test.com",
        name="Owner",
        hashed_password="h",
        is_active=True,
    )
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
    )
    agent = Agent(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Agent",
        system_prompt="You are useful.",
        model_id=model.id,
        model=model,
    )
    db.add_all(
        [
            user,
            model,
            agent,
            Conversation(agent_id=agent.id, title="UI", source="ui"),
            Conversation(agent_id=agent.id, title="API", source="api"),
        ]
    )
    await db.commit()

    rows = await chat_service.list_conversations(db, agent.id)
    page, _cursor, _has_more = await chat_service.list_conversations_page(
        db,
        agent.id,
        limit=20,
    )

    assert [row.title for row in rows] == ["UI"]
    assert [row.title for row in page] == ["UI"]
