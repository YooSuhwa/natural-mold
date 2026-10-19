"""Private MCP servers and their child tools are isolated after organization switching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


class McpReference(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID


@dataclass(frozen=True, slots=True)
class McpCase:
    client: AsyncClient
    organization: OrgFixture
    server_id: UUID


@pytest.fixture
async def mcp_case(raw_client: AsyncClient, org_fixture: OrgFixture, db: AsyncSession) -> McpCase:
    created = await raw_client.post(
        "/api/mcp-servers",
        json={"name": "Scoped MCP fixture", "transport": "sse", "url": "https://example.com/mcp"},
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201, created.text
    server_id = McpReference.model_validate_json(created.content).id
    db.add(McpTool(server_id=server_id, name="private-fixture-tool"))
    await db.commit()
    return McpCase(raw_client, org_fixture, server_id)


async def test_mcp_writer_persists_exact_company_and_org(
    db: AsyncSession, mcp_case: McpCase, private_scopes: PrivateScopes
) -> None:
    # Given: an authenticated server creation in A, with a child MCP tool.
    # When: the persisted parent is loaded independently.
    server = await db.get(McpServer, mcp_case.server_id)
    assert server is not None
    # Then: owner and company/org are explicitly recorded.
    assert (server.user_id, server.tenant_id, server.org_id) == (
        private_scopes.first.user_id,
        private_scopes.first.tenant_id,
        private_scopes.first.org_id,
    )


async def test_private_http_writer_cannot_choose_platform_system_scope(mcp_case: McpCase) -> None:
    client, org = mcp_case.client, mcp_case.organization
    created = await client.post(
        "/api/mcp-servers",
        json={
            "name": "Platform spoof",
            "transport": "sse",
            "url": "https://example.invalid/mcp",
            "is_system": True,
        },
        headers=org.session.headers(),
    )
    assert created.status_code == 201 and created.json()["is_system"] is False
    changed = await client.patch(
        f"/api/mcp-servers/{mcp_case.server_id}",
        json={"is_system": True},
        headers=org.session.headers(),
    )
    assert changed.status_code == 422


@pytest.mark.parametrize("operation", ["list", "get", "tools", "patch", "delete", "export"])
async def test_mcp_server_and_children_are_hidden_in_another_company(
    mcp_case: McpCase, operation: Literal["list", "get", "tools", "patch", "delete", "export"]
) -> None:
    # Given: the same owner selects B after registering the server in A.
    client, org = mcp_case.client, mcp_case.organization
    switched = await client.post(
        "/api/me/active-org", json={"org_id": str(org.second)}, headers=org.session.headers()
    )
    assert switched.status_code == 200
    # When: a parent, child list or mutation is requested from the other company.
    match operation:
        case "list":
            response = await client.get("/api/mcp-servers")
            assert response.status_code == 200 and response.json() == []
        case "tools":
            response = await client.get("/api/mcp-servers/all-tools")
            assert response.status_code == 200 and response.json() == []
        case "get":
            response = await client.get(f"/api/mcp-servers/{mcp_case.server_id}")
            assert response.status_code == 404
        case "patch":
            response = await client.patch(
                f"/api/mcp-servers/{mcp_case.server_id}",
                json={"name": "Foreign mutation"},
                headers=org.session.headers(),
            )
            assert response.status_code == 404
        case "delete":
            response = await client.delete(
                f"/api/mcp-servers/{mcp_case.server_id}", headers=org.session.headers()
            )
            assert response.status_code == 404
        case "export":
            response = await client.get("/api/mcp-servers/export")
            assert response.status_code == 200 and response.json()["mcpServers"] == {}
        case unreachable:
            assert_never(unreachable)
