"""Tool creation and direct access respect the user's explicitly selected organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


class ToolReference(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID


@dataclass(frozen=True, slots=True)
class ToolCase:
    client: AsyncClient
    organization: OrgFixture
    tool_id: UUID


@pytest.fixture
async def tool_case(raw_client: AsyncClient, org_fixture: OrgFixture) -> ToolCase:
    created = await raw_client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Private scope tool",
            "parameters": {"url": "https://example.com"},
        },
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201, created.text
    return ToolCase(raw_client, org_fixture, ToolReference.model_validate_json(created.content).id)


async def test_tool_writer_persists_current_company_and_org(
    db: AsyncSession, tool_case: ToolCase, private_scopes: PrivateScopes
) -> None:
    # Given: an authenticated tool creation in organization A.
    # When: the persisted ownership and scope are loaded directly.
    tool = await db.get(Tool, tool_case.tool_id)
    assert tool is not None
    # Then: the exact declared scope is present with its owner.
    assert (tool.org_id, tool.tenant_id, tool.user_id) == (
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
        private_scopes.first.user_id,
    )


@pytest.mark.parametrize("operation", ["list", "get", "patch", "delete"])
async def test_tool_in_other_org_cannot_be_read_or_changed_by_same_owner(
    tool_case: ToolCase, operation: Literal["list", "get", "patch", "delete"]
) -> None:
    # Given: the owner has switched from the creation organization to another company.
    client, org = tool_case.client, tool_case.organization
    switched = await client.post(
        "/api/me/active-org", json={"org_id": str(org.second)}, headers=org.session.headers()
    )
    assert switched.status_code == 200
    # When: the other company uses an ID owned by that same account.
    match operation:
        case "list":
            response = await client.get("/api/tools")
            # Then: no private tools from the creation org appear.
            assert response.status_code == 200 and response.json() == []
        case "get":
            response = await client.get(f"/api/tools/{tool_case.tool_id}")
            assert response.status_code == 404
        case "patch":
            response = await client.patch(
                f"/api/tools/{tool_case.tool_id}",
                json={"name": "Foreign edit"},
                headers=org.session.headers(),
            )
            assert response.status_code == 404
        case "delete":
            response = await client.delete(
                f"/api/tools/{tool_case.tool_id}", headers=org.session.headers()
            )
            assert response.status_code == 404
        case unreachable:
            assert_never(unreachable)
