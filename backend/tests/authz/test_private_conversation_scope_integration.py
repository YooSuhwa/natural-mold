"""Conversation mapper uses the actual m83 PostgreSQL history-preservation schema."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.authz.conversation_scope import load_private_conversation, resolve_conversation_actor
from app.authz.org_context import resolve_org_context
from app.authz.private_actor import PersistedActor
from app.config import settings
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from app.services.chat.conversations import create_conversation
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_m83_retained_history_keeps_its_private_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-history@example.com",
                "name": "History fixture",
                "password": "native-history-password",
            },
        )
        assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-history@example.com"))
            assert user is not None and user.last_active_org_id is not None
            context = await resolve_org_context(
                db, PersistedActor(user.id, user.is_super_user), cookie=str(user.last_active_org_id)
            )
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.flush()
            agent = Agent(
                user_id=user.id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                model_id=model.id,
                name="Removed fixture agent",
                system_prompt="Fixture",
            )
            db.add(agent)
            await db.flush()
            conversation = await create_conversation(db, agent.id, context=context)
            await db.commit()
            conversation_id = conversation.id
            owner_id = user.id
            loaded = await db.scalar(
                select(Agent).where(Agent.id == agent.id).options(selectinload(Agent.conversations))
            )
            assert loaded is not None and len(loaded.conversations) == 1
            await db.delete(loaded)
            await db.commit()
        async with stack.sessions() as db:
            context = await resolve_conversation_actor(db, conversation_id, owner_id)
            history = await load_private_conversation(db, conversation_id, context)
            assert history is not None
            assert history.agent_id is None
            assert history.agent_name_snapshot == "Removed fixture agent"
            assert history.last_activity_at.tzinfo is not None
