"""Native m83 validates all converted Agent/Builder/Trigger root writers and forced revocation."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.agent_runtime import trigger_executor
from app.config import settings
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.builder_session import BuilderSession
from app.models.model import Model
from app.models.organization import Tenant
from app.models.user import User
from app.services import trigger_service
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_m83_agent_builder_trigger_writers_and_suspension(
    monkeypatch: pytest.MonkeyPatch,
    other_company: bool,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(trigger_service, "sync_scheduler_for_trigger", AsyncMock())
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-writers@example.com",
                "name": "Writer fixture",
                "password": "native-writer-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-writers@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        created = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Native agent",
                "system_prompt": "Fixture",
                "model_id": str(model_id),
                "identity_mode": "fixed",
            },
        )
        assert created.status_code == 201, created.text
        agent_id = UUID(created.json()["id"])
        builder = await stack.client.post(
            "/api/builder", headers=headers, json={"user_request": "Native builder fixture"}
        )
        assert builder.status_code == 201, builder.text
        builder_id = UUID(builder.json()["id"])
        trigger = await stack.client.post(
            f"/api/agents/{agent_id}/triggers",
            headers=headers,
            json={
                "trigger_type": "interval",
                "schedule_config": {"interval_minutes": 15},
                "input_message": "Fixture",
            },
        )
        assert trigger.status_code == 201, trigger.text
        trigger_id = UUID(trigger.json()["id"])
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-writers@example.com"))
            assert user is not None
            agent_row = await db.get(Agent, agent_id)
            builder_row = await db.get(BuilderSession, builder_id)
            trigger_row = await db.get(AgentTrigger, trigger_id)
            assert agent_row is not None and builder_row is not None and trigger_row is not None
            for row in (agent_row, builder_row, trigger_row):
                assert row.user_id == user.id and row.org_id == user.last_active_org_id
                assert row.tenant_id == agent_row.tenant_id
            await db.execute(
                update(Tenant).where(Tenant.id == trigger_row.tenant_id).values(status="suspended")
            )
            await db.commit()
        monkeypatch.setattr(trigger_executor, "async_session", stack.sessions)
        run = await trigger_executor.execute_trigger(str(trigger_id), force=True)
        assert run is None
        async with stack.sessions() as db:
            assert (
                await db.scalar(
                    select(AgentTriggerRun.id).where(AgentTriggerRun.trigger_id == trigger_id)
                )
                is None
            )
            trigger_row = await db.get(AgentTrigger, trigger_id)
            assert trigger_row is not None and trigger_row.status == "active"
            assert trigger_row.failure_count == 0
