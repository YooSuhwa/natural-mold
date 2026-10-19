"""Private resource HTTP behavior uses the deployed m83 schema in owned PostgreSQL."""

from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.tool import Tool
from app.models.user import User
from tests.authz.test_private_credential_scope import CredentialReference
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["credential", "tool", "mcp"])
async def test_m83_resource_scope_survives_same_owner_company_switch(
    monkeypatch: pytest.MonkeyPatch, kind: Literal["credential", "tool", "mcp"]
) -> None:
    # Given: a real registered account creates a private credential in its current org.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-private-credential@example.com",
                "name": "Fixture",
                "password": "native-scope-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        match kind:
            case "credential":
                endpoint = "/api/credentials"
                payload = {
                    "definition_key": "http_bearer",
                    "name": "Private native fixture",
                    "data": {"token": "dummy-native-only"},
                }
            case "tool":
                endpoint = "/api/tools"
                payload = {
                    "definition_key": "http_request",
                    "name": "Native tool fixture",
                    "parameters": {"url": "https://example.com"},
                }
            case "mcp":
                endpoint = "/api/mcp-servers"
                payload = {
                    "name": "Native MCP fixture",
                    "transport": "sse",
                    "url": "https://example.com/mcp",
                }
            case unreachable:
                assert_never(unreachable)
        created = await stack.client.post(endpoint, json=payload, headers=headers)
        assert created.status_code == 201, created.text
        credential_id = CredentialReference.model_validate_json(created.content).id
        async with stack.sessions() as db:
            user = await db.scalar(
                select(User).where(User.email == "native-private-credential@example.com")
            )
            assert user is not None
            match kind:
                case "credential":
                    credential = await db.get(Credential, credential_id)
                case "tool":
                    credential = await db.get(Tool, credential_id)
                case "mcp":
                    credential = await db.get(McpServer, credential_id)
                case unreachable:
                    assert_never(unreachable)
            assert credential is not None and credential.org_id == user.last_active_org_id
            first_org = await db.get(Organization, credential.org_id)
            assert first_org is not None and credential.tenant_id == first_org.tenant_id
            tenant = Tenant(
                name="Native second company",
                slug="native-second",
                secrets_namespace="native-second",
            )
            db.add(tenant)
            await db.flush()
            second = Organization(
                name="Native second org", slug="native-second", tenant_id=tenant.id
            )
            db.add(second)
            await db.flush()
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id))
            db.add(OrganizationMember(org_id=second.id, tenant_id=tenant.id, user_id=user.id))
            await db.commit()
            second_id: UUID = second.id
        switched = await stack.client.post(
            "/api/me/active-org", json={"org_id": str(second_id)}, headers=headers
        )
        assert switched.status_code == 200, switched.text
        # When: that same owner requests the first company's credential in the second.
        response = await stack.client.get(f"{endpoint}/{credential_id}")
        # Then: the physical scope and real request guard deny the foreign-company ID.
        assert response.status_code == 404, response.text
        created_second = await stack.client.post(endpoint, json=payload, headers=headers)
        assert created_second.status_code == 201, created_second.text
        second_resource_id = CredentialReference.model_validate_json(created_second.content).id
        async with stack.sessions() as db:
            match kind:
                case "credential":
                    second_resource = await db.get(Credential, second_resource_id)
                case "tool":
                    second_resource = await db.get(Tool, second_resource_id)
                case "mcp":
                    second_resource = await db.get(McpServer, second_resource_id)
                case unreachable:
                    assert_never(unreachable)
            assert second_resource is not None
            assert second_resource.user_id == user.id and second_resource.org_id == second_id
            assert second_resource.tenant_id == tenant.id
