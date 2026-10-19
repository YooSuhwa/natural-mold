"""Actual m83 PostgreSQL accepts key/deployment/conversation scope and denies suspension."""

from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import Tenant
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_m83_scoped_api_key_and_thread_writer(
    monkeypatch: pytest.MonkeyPatch,
    other_company: bool,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-api-scope@example.com",
                "name": "API fixture",
                "password": "native-api-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-api-scope@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Native API fixture",
                "system_prompt": "Fixture",
                "model_id": str(model_id),
                "identity_mode": "fixed",
            },
        )
        assert agent.status_code == 201, agent.text
        deployment = await stack.client.post(
            "/api/agent-api/deployments", headers=headers, json={"agent_id": agent.json()["id"]}
        )
        assert deployment.status_code == 201, deployment.text
        key = await stack.client.post(
            "/api/agent-api/keys",
            headers=headers,
            json={
                "name": "Native key fixture",
                "scopes": ["read", "invoke"],
                "allow_all_deployments": True,
            },
        )
        assert key.status_code == 201
        bearer = {"Authorization": f"Bearer {key.json()['key']}"}
        thread = await stack.client.post(
            "/v1/threads", headers=bearer, json={"agent_id": deployment.json()["public_id"]}
        )
        assert thread.status_code == 201, thread.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-api-scope@example.com"))
            key_row = await db.get(AgentApiKey, UUID(key.json()["id"]))
            deployment_row = await db.get(AgentDeployment, UUID(deployment.json()["id"]))
            conversation = await db.get(Conversation, UUID(thread.json()["conversation_id"]))
            assert (
                user is not None
                and key_row is not None
                and deployment_row is not None
                and conversation is not None
            )
            for row in (key_row, deployment_row, conversation):
                assert row.user_id == user.id and row.org_id == user.last_active_org_id
                assert row.tenant_id == key_row.tenant_id
            await db.execute(
                update(Tenant).where(Tenant.id == key_row.tenant_id).values(status="suspended")
            )
            await db.commit()
        denied = await stack.client.get("/v1/agents", headers=bearer)
        assert denied.status_code == 401, denied.text
