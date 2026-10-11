"""Real m83 Assistant tool writes recheck the captured actor on every invocation."""

from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.agent_runtime.assistant.tools import write_tools
from app.authz.agent_scope import resolve_agent_actor
from app.config import settings
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_assistant_write_stops_after_actor_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-assistant@example.com",
                "name": "Assistant fixture",
                "password": "native-assistant-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        created = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Assistant fixture",
                "system_prompt": "Original fixture",
                "model_id": str(model_id),
            },
        )
        assert created.status_code == 201, created.text
        agent_id = UUID(created.json()["id"])
        monkeypatch.setattr(write_tools, "async_session_factory", stack.sessions)
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-assistant@example.com"))
            assert user is not None
            user_id = user.id
            context = await resolve_agent_actor(db, agent_id, user_id)
            tools = write_tools.build_write_tools(db, agent_id, context)
        edit = next(tool for tool in tools if tool.name == "edit_system_prompt")
        await edit.ainvoke({"old_string": "Original fixture", "new_string": "Updated fixture"})
        async with stack.sessions() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None and agent.system_prompt == "Updated fixture"
            await db.execute(update(User).where(User.id == user_id).values(is_active=False))
            await db.commit()
        denied = await edit.ainvoke(
            {"old_string": "Updated fixture", "new_string": "Denied overwrite"}
        )
        assert denied == "에이전트를 찾을 수 없습니다."
        async with stack.sessions() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None and agent.system_prompt == "Updated fixture"
