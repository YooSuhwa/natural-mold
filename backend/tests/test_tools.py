from __future__ import annotations

import pytest
from httpx import AsyncClient


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
