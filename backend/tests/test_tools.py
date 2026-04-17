from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import MCPServer, Tool

# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_tool(client: AsyncClient):
    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Weather API",
            "description": "Get weather for a city",
            "api_url": "https://api.weather.com/v1",
            "http_method": "GET",
            "parameters_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "auth_type": "api_key",
        },
    )
    assert resp.status_code == 201
    tool = resp.json()
    assert tool["name"] == "Weather API"
    assert tool["type"] == "custom"
    tool_id = tool["id"]

    # List
    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Delete
    resp = await client.delete(f"/api/tools/{tool_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/tools")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_register_mcp_server(client: AsyncClient):
    resp = await client.post(
        "/api/tools/mcp-server",
        json={
            "name": "Google Workspace MCP",
            "url": "https://mcp.google-workspace.com",
            "auth_type": "api_key",
        },
    )
    assert resp.status_code == 201
    server = resp.json()
    assert server["name"] == "Google Workspace MCP"
    assert server["status"] == "active"


@pytest.mark.asyncio
async def test_delete_nonexistent_tool(client: AsyncClient):
    resp = await client.delete("/api/tools/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_custom_tool_no_credential(client: AsyncClient):
    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My API",
            "api_url": "https://example.com",
            "http_method": "GET",
        },
    )
    assert resp.status_code == 201
    tool = resp.json()
    assert tool["credential_id"] is None


@pytest.mark.asyncio
async def test_patch_tool_auth_config_preserves_unset_fields(
    client: AsyncClient, db: AsyncSession
):
    """PATCH with only auth_config must NOT wipe credential_id, and vice versa."""
    server = MCPServer(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        name="Test MCP",
        url="https://example.com",
        auth_type="none",
    )
    db.add(server)
    await db.flush()

    tool = Tool(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        type="mcp",
        mcp_server_id=server.id,
        name="seed_tool",
        auth_type="api_key",
        auth_config={"api_key": "initial"},
    )
    db.add(tool)
    await db.commit()

    # PATCH only auth_config — credential_id should remain null, not be forced null
    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"auth_config": {"api_key": "updated"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_config"] == {"api_key": "updated"}
    assert body["credential_id"] is None

    # PATCH only credential_id=null — auth_config should be preserved (not wiped to {})
    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"credential_id": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_config"] == {"api_key": "updated"}


@pytest.mark.asyncio
async def test_patch_mcp_tool_rejects_other_user(client: AsyncClient, db: AsyncSession):
    """IDOR: a user must not be able to modify another user's MCP tool."""
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    server = MCPServer(
        user_id=other_user_id,
        name="Stranger MCP",
        url="https://example.com",
        auth_type="none",
    )
    db.add(server)
    await db.flush()
    tool = Tool(
        user_id=other_user_id,
        type="mcp",
        mcp_server_id=server.id,
        name="stranger_tool",
        auth_type="none",
    )
    db.add(tool)
    await db.commit()

    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"auth_config": {"api_key": "hijack"}},
    )
    assert resp.status_code == 404
