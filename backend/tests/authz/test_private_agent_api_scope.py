"""Private API keys remain in their persisted org across UI switches and detached work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service, service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import OrganizationMember, Tenant
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate
from app.services import agent_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class ApiCase:
    key: AgentApiKey
    first: AgentDeployment
    second: AgentDeployment
    cleartext: str
    client: AsyncClient


@pytest.fixture
async def api_case(
    db: AsyncSession, private_scopes: PrivateScopes, raw_client: AsyncClient
) -> ApiCase:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    deployments: list[AgentDeployment] = []
    for context in (private_scopes.first, private_scopes.second):
        agent = await agent_service.create_agent(
            db,
            AgentCreate(
                name="API fixture",
                system_prompt="Fixture",
                model_id=model.id,
                identity_mode="fixed",
            ),
            context,
        )
        deployment = await service.create_deployment(
            db, context, AgentDeploymentCreate(agent_id=agent.id)
        )
        deployments.append(deployment)
    key, cleartext = await service.create_api_key(
        db,
        private_scopes.first,
        AgentApiKeyCreate(
            name="Scoped fixture", scopes=["read", "invoke"], allow_all_deployments=True
        ),
    )
    return ApiCase(key, deployments[0], deployments[1], cleartext, raw_client)


async def test_key_and_deployment_writers_and_control_lists_use_exact_scope(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes
) -> None:
    for row in (api_case.key, api_case.first):
        assert (row.user_id, row.org_id, row.tenant_id) == (
            private_scopes.first.user_id,
            private_scopes.first.org_id,
            private_scopes.first.tenant_id,
        )
    assert [row.id for row in await service.list_deployments(db, private_scopes.second)] == [
        api_case.second.id
    ]
    assert await service.list_api_keys(db, private_scopes.second) == []
    with pytest.raises(AppError) as captured:
        await service.revoke_api_key(db, private_scopes.second, api_case.key.id)
    assert captured.value.status == 404
    assert api_case.key.revoked_at is None


async def test_runtime_key_ignores_ui_org_and_allow_all_cannot_cross_company(
    raw_client: AsyncClient, api_case: ApiCase, private_scopes: PrivateScopes
) -> None:
    headers = {"Authorization": f"Bearer {api_case.cleartext}"}
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    listed = await raw_client.get("/v1/agents", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["data"]] == [api_case.first.public_id]
    foreign = await raw_client.post(
        "/v1/threads", headers=headers, json={"agent_id": api_case.second.public_id}
    )
    assert foreign.status_code == 404, foreign.text
    own = await raw_client.post(
        "/v1/threads", headers=headers, json={"agent_id": api_case.first.public_id}
    )
    assert own.status_code == 201, own.text


@pytest.mark.parametrize("change", ["membership", "company", "account"])
async def test_runtime_key_rejects_revoked_authority_before_usage_write(
    db: AsyncSession, api_case: ApiCase, change: Literal["membership", "company", "account"]
) -> None:
    match change:
        case "membership":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == api_case.key.org_id,
                    OrganizationMember.user_id == api_case.key.user_id,
                )
            )
        case "company":
            await db.execute(
                update(Tenant).where(Tenant.id == api_case.key.tenant_id).values(status="suspended")
            )
        case "account":
            await db.execute(
                update(User).where(User.id == api_case.key.user_id).values(is_active=False)
            )
    await db.commit()
    response = await api_case.client.get(
        "/v1/agents", headers={"Authorization": f"Bearer {api_case.cleartext}"}
    )
    assert response.status_code == 401, response.text
    await db.refresh(api_case.key)
    assert api_case.key.usage_count == 0


async def test_detached_api_preparation_rechecks_key_revocation_and_persisted_scope(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes
) -> None:
    principal = ApiKeyPrincipal(
        key=api_case.key, user_id=api_case.key.user_id, context=private_scopes.first
    )
    conversation = await runtime_service.create_api_conversation(
        db, api_case.first, principal=principal
    )
    stored = await db.scalar(select(Conversation).where(Conversation.id == conversation.id))
    assert stored is not None
    assert (stored.user_id, stored.org_id, stored.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
    await db.execute(
        update(AgentApiKey)
        .where(AgentApiKey.id == api_case.key.id)
        .values(revoked_at=service.utc_now_naive())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert api_case.key.revoked_at is None
    with pytest.raises(AppError) as captured:
        await runtime_service.create_api_conversation(db, api_case.first, principal=principal)
    assert captured.value.code == "AGENT_API_KEY_REVOKED"


async def test_runtime_config_rechecks_removed_invoke_scope_before_policy_or_credentials(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes
) -> None:
    principal = ApiKeyPrincipal(
        key=api_case.key, user_id=api_case.key.user_id, context=private_scopes.first
    )
    conversation = await runtime_service.create_api_conversation(
        db, api_case.first, principal=principal
    )
    await db.execute(
        update(AgentApiKey)
        .where(AgentApiKey.id == api_case.key.id)
        .values(scopes=["read"])
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert "invoke" in api_case.key.scopes
    with pytest.raises(AppError) as captured:
        await runtime_service.build_config_for_run(
            db,
            principal=principal,
            deployment=api_case.first,
            conversation=conversation,
            external_user=None,
            required_scope="invoke",
        )
    assert captured.value.code == "AGENT_API_SCOPE_REQUIRED"
