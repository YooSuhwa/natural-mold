from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, TestSession


async def seed_artifact_conversation() -> tuple[uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        if await db.get(User, TEST_USER_ID) is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await ensure_unit_scope(
            db, CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test User")
        )
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name=f"Artifact Tester {uuid.uuid4().hex[:8]}",
            description=None,
            system_prompt="Write files when asked.",
            model_id=model.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_name_snapshot=agent.name,
            title="Artifacts",
            last_activity_at=datetime.now(UTC),
        )
        db.add(conv)
        await db.commit()
        return conv.id, agent.id
