"""Extended tests for app.routers.tools — MCP server, auth config, edge cases."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.tool import MCPServer, Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user() -> None:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        await db.commit()


async def _seed_mcp_server() -> uuid.UUID:
    async with TestSession() as db:
        server = MCPServer(
            user_id=TEST_USER_ID,
            name="Test MCP",
            url="https://mcp.example.com",
            auth_type="api_key",
            auth_config={"api_key": "test-key"},
        )
        db.add(server)
        await db.commit()
        return server.id


async def _seed_prebuilt_tool() -> uuid.UUID:
    async with TestSession() as db:
        tool = Tool(
            type="prebuilt",
            is_system=True,
            name="Naver Blog Search",
            description="네이버 블로그 검색",
        )
        db.add(tool)
        await db.commit()
        return tool.id


# ---------------------------------------------------------------------------
# POST /api/tools/mcp-server/{id}/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_mcp_connection_success(client: AsyncClient):
    await _seed_user()
    server_id = await _seed_mcp_server()

    mock_result = {
        "success": True,
        "server_info": {"name": "test", "version": "1.0"},
        "tools": [{"name": "tool1"}],
    }

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        resp = await client.post(f"/api/tools/mcp-server/{server_id}/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["tools"]) == 1


@pytest.mark.asyncio
async def test_test_mcp_connection_server_not_found(client: AsyncClient):
    await _seed_user()
    fake_id = "00000000-0000-0000-0000-000000000099"

    resp = await client.post(f"/api/tools/mcp-server/{fake_id}/test")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/tools/{id}/auth-config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_auth_config_prebuilt_tool(client: AsyncClient):
    await _seed_user()
    tool_id = await _seed_prebuilt_tool()

    resp = await client.patch(
        f"/api/tools/{tool_id}/auth-config",
        json={"auth_config": {"naver_client_id": "new-id", "naver_client_secret": "new-secret"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    # ToolResponse masks string values to avoid leaking secrets via API
    assert data["auth_config"]["naver_client_id"] == "***"
    assert data["auth_config"]["naver_client_secret"] == "***"


@pytest.mark.asyncio
async def test_update_auth_config_tool_not_found(client: AsyncClient):
    await _seed_user()
    fake_id = "00000000-0000-0000-0000-000000000099"

    resp = await client.patch(
        f"/api/tools/{fake_id}/auth-config",
        json={"auth_config": {"key": "val"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_auth_config_other_user_custom_returns_404(client: AsyncClient):
    """Updating auth_config on another user's CUSTOM tool returns 404 (IDOR guard)."""
    await _seed_user()

    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    async with TestSession() as db:
        tool = Tool(
            type="custom",
            user_id=other_user_id,
            name="Stranger Custom",
            description="custom tool",
        )
        db.add(tool)
        await db.commit()
        tool_id = tool.id

    resp = await client.patch(
        f"/api/tools/{tool_id}/auth-config",
        json={"auth_config": {"key": "val"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mcp_server_register_via_api(client: AsyncClient):
    """POST /api/tools/mcp-server should create and return the server."""
    await _seed_user()

    resp = await client.post(
        "/api/tools/mcp-server",
        json={
            "name": "New MCP Server",
            "url": "https://new-mcp.example.com",
            "auth_type": "none",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "New MCP Server"
    assert data["status"] == "active"
    assert data["tools"] == []


# ---------------------------------------------------------------------------
# GET /api/tools — ToolResponse.provider_name (M3 hotfix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_exposes_provider_name_for_prebuilt(client: AsyncClient):
    """PREBUILT tool should expose `provider_name` in ToolResponse; BUILTIN stays null.

    Regression fence for Pichai's schemas/tool.py:64 hotfix. Without this field the
    frontend ConnectionBindingDialog cannot scope by provider and falls back to
    legacy auth_config writes — the exact ADR-008 §문제 1 regression we are here to
    prevent.
    """
    await _seed_user()

    async with TestSession() as db:
        naver = Tool(
            type="prebuilt",
            is_system=True,
            provider_name="naver",
            name="Naver Blog Search",
            description="네이버 블로그 검색",
        )
        web_search = Tool(
            type="builtin",
            is_system=True,
            provider_name=None,
            name="Web Search",
            description="DuckDuckGo web search (no credentials)",
        )
        db.add_all([naver, web_search])
        await db.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    tools = resp.json()

    by_name = {t["name"]: t for t in tools}
    assert "Naver Blog Search" in by_name
    assert "Web Search" in by_name

    assert by_name["Naver Blog Search"]["provider_name"] == "naver"
    assert by_name["Naver Blog Search"]["type"] == "prebuilt"
    assert by_name["Web Search"]["provider_name"] is None
    assert by_name["Web Search"]["type"] == "builtin"
