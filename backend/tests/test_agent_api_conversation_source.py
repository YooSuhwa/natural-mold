from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.dependencies import CurrentUser
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)
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
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    db.add_all(
        [
            user,
            model,
            agent,
            Conversation(
                agent_id=agent.id,
                title="UI",
                source="ui",
                user_id=TEST_USER_ID,
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                agent_name_snapshot="Agent",
                last_activity_at=datetime.now(UTC),
            ),
            Conversation(
                agent_id=agent.id,
                title="API",
                source="api",
                user_id=TEST_USER_ID,
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                agent_name_snapshot="Agent",
                last_activity_at=datetime.now(UTC),
            ),
        ]
    )
    await db.flush()
    await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
    await db.commit()

    rows = await chat_service.list_conversations(db, agent.id, context=unit_context(TEST_USER_ID))
    page, _cursor, _has_more = await chat_service.list_conversations_page(
        db, agent.id, limit=20, context=unit_context(TEST_USER_ID)
    )

    assert [row.title for row in rows] == ["UI"]
    assert [row.title for row in page] == ["UI"]
