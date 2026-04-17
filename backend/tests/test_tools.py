from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.credential import Credential
from app.models.model import Model
from app.models.tool import AgentToolLink, MCPServer, Tool
from app.services.chat_service import build_tools_config, get_agent_with_tools
from tests.conftest import TEST_USER_ID

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

    # PATCH only auth_config — credential_id should remain null, not be forced null.
    # ToolResponse masks string values in auth_config, so the response shows "***"
    # while the underlying DB row keeps the real value (verified separately below).
    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"auth_config": {"api_key": "updated"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_config"] == {"api_key": "***"}
    assert body["credential_id"] is None

    # PATCH only credential_id=null — auth_config should be preserved (not wiped to {})
    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"credential_id": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_config"] == {"api_key": "***"}

    # Confirm the underlying value was actually persisted (and not the mask)
    await db.refresh(tool)
    assert tool.auth_config == {"api_key": "updated"}


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


@pytest.mark.asyncio
async def test_tool_response_masks_auth_config_string_values(
    client: AsyncClient, db: AsyncSession
):
    """GET /api/tools must not leak plaintext auth_config values (legacy data).

    Tools created before centralised credentials may still hold inline secrets
    in `auth_config`. The API response masks string values with "***" while
    preserving keys so the UI can detect "configured" state.
    """
    server = MCPServer(
        user_id=TEST_USER_ID,
        name="Legacy MCP",
        url="https://example.com",
        auth_type="api_key",
    )
    db.add(server)
    await db.flush()
    tool = Tool(
        user_id=TEST_USER_ID,
        type="mcp",
        mcp_server_id=server.id,
        name="legacy_tool",
        auth_type="api_key",
        auth_config={"api_key": "real-secret", "extra": ""},
    )
    db.add(tool)
    await db.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    found = next((t for t in resp.json() if t["id"] == str(tool.id)), None)
    assert found is not None
    # Non-empty string masked to "***"; empty string left as-is (still falsy)
    assert found["auth_config"] == {"api_key": "***", "extra": ""}

    # Round-trip protection: client must not be able to PATCH the mask back in.
    resp = await client.patch(
        f"/api/tools/{tool.id}/auth-config",
        json={"auth_config": {"api_key": "***"}},
    )
    assert resp.status_code == 422
    # Server's stored value is unchanged
    await db.refresh(tool)
    assert tool.auth_config == {"api_key": "real-secret", "extra": ""}


# ---------------------------------------------------------------------------
# MCP server group endpoints (M1: list / patch / delete)
# ---------------------------------------------------------------------------


async def _seed_mcp_server_with_tools(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str = "Test MCP",
    tool_count: int = 3,
    credential_id: uuid.UUID | None = None,
    auth_config: dict | None = None,
) -> MCPServer:
    server = MCPServer(
        user_id=user_id,
        name=name,
        url="https://example.com/mcp",
        auth_type="api_key",
        auth_config=auth_config,
        credential_id=credential_id,
    )
    db.add(server)
    await db.flush()
    for i in range(tool_count):
        db.add(
            Tool(
                user_id=user_id,
                type="mcp",
                mcp_server_id=server.id,
                name=f"tool_{i}",
                auth_type="api_key",
            )
        )
    await db.commit()
    await db.refresh(server, ["tools"])
    return server


@pytest.mark.asyncio
async def test_list_mcp_servers_returns_tool_count(
    client: AsyncClient, db: AsyncSession
):
    """GET /api/tools/mcp-servers returns aggregated tool_count per server."""
    await _seed_mcp_server_with_tools(db, name="Server A", tool_count=4)
    await _seed_mcp_server_with_tools(db, name="Server B", tool_count=1)

    resp = await client.get("/api/tools/mcp-servers")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    by_name = {item["name"]: item for item in items}
    assert by_name["Server A"]["tool_count"] == 4
    assert by_name["Server B"]["tool_count"] == 1
    # credential brief absent when no credential is linked
    assert by_name["Server A"]["credential"] is None
    assert by_name["Server A"]["credential_id"] is None


@pytest.mark.asyncio
async def test_update_mcp_server_credential(
    client: AsyncClient, db: AsyncSession, monkeypatch
):
    """PATCH /api/tools/mcp-servers/{id} updates credential_id and rejects other users."""
    monkeypatch.setattr(
        "app.services.encryption._get_fernet", lambda: None, raising=False
    )

    cred = Credential(
        user_id=TEST_USER_ID,
        name="MCP Key",
        credential_type="mcp",
        provider_name="custom",
        data_encrypted=json.dumps({"api_key": "secret"}),
    )
    db.add(cred)
    await db.flush()
    server = await _seed_mcp_server_with_tools(db, name="Server X")

    resp = await client.patch(
        f"/api/tools/mcp-servers/{server.id}",
        json={"credential_id": str(cred.id), "name": "Renamed Server"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["credential_id"] == str(cred.id)
    assert body["name"] == "Renamed Server"

    # Other user's server returns 404
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_server = await _seed_mcp_server_with_tools(
        db, user_id=other_user_id, name="Stranger Server"
    )
    resp = await client.patch(
        f"/api/tools/mcp-servers/{other_server.id}",
        json={"name": "hijack"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_mcp_server_unset_credential(
    client: AsyncClient, db: AsyncSession
):
    """PATCH with {credential_id: null} clears the credential link."""
    cred = Credential(
        user_id=TEST_USER_ID,
        name="MCP Key",
        credential_type="mcp",
        provider_name="custom",
        data_encrypted=json.dumps({"api_key": "secret"}),
    )
    db.add(cred)
    await db.flush()
    server = await _seed_mcp_server_with_tools(
        db, name="Server Y", credential_id=cred.id
    )

    resp = await client.patch(
        f"/api/tools/mcp-servers/{server.id}",
        json={"credential_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["credential_id"] is None

    await db.refresh(server)
    assert server.credential_id is None


@pytest.mark.asyncio
async def test_delete_mcp_server_cascades_tools(
    client: AsyncClient, db: AsyncSession
):
    """DELETE /api/tools/mcp-servers/{id} removes the server and all child tools."""
    server = await _seed_mcp_server_with_tools(db, name="Doomed", tool_count=5)
    server_id = server.id
    tool_ids = [t.id for t in server.tools]
    assert len(tool_ids) == 5

    resp = await client.delete(f"/api/tools/mcp-servers/{server_id}")
    assert resp.status_code == 204

    from sqlalchemy import select

    server_check = await db.execute(
        select(MCPServer).where(MCPServer.id == server_id)
    )
    assert server_check.scalar_one_or_none() is None

    tool_check = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
    assert tool_check.scalars().all() == []

    # Deleting a missing server returns 404
    resp = await client.delete(f"/api/tools/mcp-servers/{server_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_build_tools_config_mcp_uses_server_credential(db: AsyncSession):
    """MCP tools resolve auth from the server's credential, ignoring tool-level fields."""
    with patch(
        "app.services.chat_service.resolve_credential_data",
        return_value={"api_key": "from-server-cred"},
    ):
        model = Model(
            id=uuid.uuid4(),
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
        )
        db.add(model)
        await db.flush()

        server_cred = Credential(
            user_id=TEST_USER_ID,
            name="Server Cred",
            credential_type="mcp",
            provider_name="custom",
            data_encrypted="ignored",
        )
        db.add(server_cred)
        await db.flush()

        server = MCPServer(
            user_id=TEST_USER_ID,
            name="Server",
            url="https://example.com",
            auth_type="api_key",
            credential_id=server_cred.id,
        )
        db.add(server)
        await db.flush()

        tool = Tool(
            user_id=TEST_USER_ID,
            type="mcp",
            mcp_server_id=server.id,
            name="srv_tool",
            auth_type="api_key",
            # tool-level junk that must be ignored
            auth_config={"api_key": "from-tool-auth"},
        )
        db.add(tool)
        await db.flush()

        agent = Agent(
            user_id=TEST_USER_ID,
            name="Agent",
            system_prompt="hi",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
        await db.commit()

        loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
        assert loaded is not None
        configs = build_tools_config(loaded)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg["type"] == "mcp"
        assert cfg["auth_config"] == {"api_key": "from-server-cred"}
        assert cfg["mcp_server_url"] == "https://example.com"
