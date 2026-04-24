from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connection import Connection
from app.models.credential import Credential
from app.models.model import Model
from app.models.tool import AgentToolLink, MCPServer, Tool
from app.services.chat_service import build_tools_config, get_agent_with_tools
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_tool(client: AsyncClient, db: AsyncSession):
    # M6: CUSTOM tool 생성은 connection_id 필수 — credential + connection 선행 세팅
    credential = Credential(
        user_id=TEST_USER_ID,
        name="Weather Key",
        credential_type="api_key",
        provider_name="custom",
        data_encrypted=json.dumps({"api_key": "secret"}),
        field_keys=["api_key"],
    )
    db.add(credential)
    await db.flush()
    connection = Connection(
        user_id=TEST_USER_ID,
        type="custom",
        provider_name="custom",
        display_name="Weather",
        credential_id=credential.id,
    )
    db.add(connection)
    await db.commit()

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
            "connection_id": str(connection.id),
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
async def test_custom_tool_requires_connection(client: AsyncClient):
    """M6: CUSTOM tool 생성 시 connection_id 누락 → 422 (pydantic invariant)."""
    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My API",
            "api_url": "https://example.com",
            "http_method": "GET",
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_custom_tool(
    *, user_id: uuid.UUID = None, name: str = "my_custom_tool", **overrides
) -> Tool:
    if user_id is None:
        user_id = TEST_USER_ID
    return Tool(
        user_id=user_id,
        type="custom",
        name=name,
        api_url="https://example.com/api",
        http_method="GET",
        auth_type="api_key",
        **overrides,
    )


def _make_credential(
    *,
    user_id: uuid.UUID = None,
    name: str = "Custom Key",
    provider_name: str = "custom",
    data: dict | None = None,
) -> Credential:
    if user_id is None:
        user_id = TEST_USER_ID
    return Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=json.dumps(data or {"api_key": "secret"}),
    )


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
    """MCP tools (legacy mcp_server path) resolve auth from the server's credential.

    M6.1 까지 legacy fallback (`tools.mcp_server_id` → `mcp_servers.credential_id`)
    이 유지된다. 이 테스트는 tool-level inline auth 없이도 server 경로가 동작
    하는지만 검증한다.
    """
    with patch(
        "app.services.credential_service.resolve_credential_data",
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


# ---------------------------------------------------------------------------
# PATCH /api/tools/{id} — connection_id (M6.1 옵션 D)
# ---------------------------------------------------------------------------


async def _seed_credential_and_connection(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    conn_type: str = "custom",
    provider_name: str = "custom",
    display_name: str = "Test Connection",
) -> Connection:
    cred = Credential(
        user_id=user_id,
        name=f"{display_name} Key",
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=json.dumps({"api_key": "secret"}),
        field_keys=["api_key"],
    )
    db.add(cred)
    await db.flush()
    conn = Connection(
        user_id=user_id,
        type=conn_type,
        provider_name=provider_name,
        display_name=display_name,
        credential_id=cred.id,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_patch_tool_connection_id_custom_success(
    client: AsyncClient, db: AsyncSession
):
    """CUSTOM tool + CUSTOM connection → 200, connection_id 반영."""
    initial_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Initial"
    )
    new_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="New"
    )
    tool = _make_custom_tool(connection_id=initial_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(new_conn.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connection_id"] == str(new_conn.id)


@pytest.mark.asyncio
async def test_patch_tool_connection_id_mcp_success(
    client: AsyncClient, db: AsyncSession
):
    """MCP tool + MCP connection → 200."""
    mcp_conn = await _seed_credential_and_connection(
        db,
        conn_type="mcp",
        provider_name="custom",
        display_name="MCP Conn",
    )
    tool = Tool(
        user_id=TEST_USER_ID,
        type="mcp",
        name="my_mcp_tool",
        auth_type="api_key",
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(mcp_conn.id)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] == str(mcp_conn.id)


@pytest.mark.asyncio
async def test_patch_tool_connection_id_prebuilt_400(
    client: AsyncClient, db: AsyncSession
):
    """PREBUILT system tool → 400 (PREBUILT는 (user_id, provider_name) 스코프)."""
    tool = Tool(
        type="prebuilt",
        is_system=True,
        provider_name="naver",
        name="Naver Blog Search",
        description="네이버 블로그 검색",
        auth_type="api_key",
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    prebuilt_conn = await _seed_credential_and_connection(
        db, conn_type="prebuilt", provider_name="naver", display_name="Naver Conn"
    )

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(prebuilt_conn.id)},
    )
    assert resp.status_code == 400, resp.text
    assert "PREBUILT" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_tool_connection_id_other_user_connection_404(
    client: AsyncClient, db: AsyncSession
):
    """다른 유저의 connection → 404 (IDOR 방지)."""
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_conn = await _seed_credential_and_connection(
        db, user_id=other_user_id, conn_type="custom", display_name="Other"
    )
    own_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Own"
    )
    tool = _make_custom_tool(connection_id=own_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(other_conn.id)},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_patch_tool_connection_id_type_mismatch_422(
    client: AsyncClient, db: AsyncSession
):
    """CUSTOM tool + MCP connection → 422 (type 정합성)."""
    own_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Own Custom"
    )
    mcp_conn = await _seed_credential_and_connection(
        db, conn_type="mcp", display_name="MCP Wrong"
    )
    tool = _make_custom_tool(connection_id=own_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(mcp_conn.id)},
    )
    assert resp.status_code == 422, resp.text
    assert "does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_tool_connection_id_none_clears_binding(
    client: AsyncClient, db: AsyncSession
):
    """None으로 설정 → connection_id NULL (해제)."""
    conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="ToClear"
    )
    tool = _make_custom_tool(connection_id=conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] is None


@pytest.mark.asyncio
async def test_patch_tool_nonexistent_404(client: AsyncClient):
    """존재하지 않는 tool → 404."""
    resp = await client.patch(
        "/api/tools/00000000-0000-0000-0000-000000000999",
        json={"connection_id": None},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_tool_unknown_field_422(
    client: AsyncClient, db: AsyncSession
):
    """`extra="forbid"` — 알 수 없는 필드는 422."""
    conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Extra"
    )
    tool = _make_custom_tool(connection_id=conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"name": "renamed"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_patch_tool_other_user_owned_tool_404(
    client: AsyncClient, db: AsyncSession
):
    """다른 유저 소유의 user-tool PATCH → 404 (정보 노출 방지)."""
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_conn = await _seed_credential_and_connection(
        db, user_id=other_user_id, conn_type="custom", display_name="Other Conn"
    )
    other_tool = _make_custom_tool(
        user_id=other_user_id, name="strangers_tool", connection_id=other_conn.id
    )
    db.add(other_tool)
    await db.commit()
    await db.refresh(other_tool)

    resp = await client.patch(
        f"/api/tools/{other_tool.id}",
        json={"connection_id": None},
    )
    assert resp.status_code == 404
