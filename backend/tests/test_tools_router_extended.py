"""Extended tests for app.routers.tools — MCP server, auth config, edge cases."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.connection import Connection
from app.models.credential import Credential
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


# ---------------------------------------------------------------------------
# POST /api/tools/custom — connection_id binding (M4)
# ---------------------------------------------------------------------------


async def _seed_custom_credential(user_id: uuid.UUID) -> uuid.UUID:
    async with TestSession() as db:
        cred = Credential(
            user_id=user_id,
            name="Custom API Key",
            credential_type="api_key",
            provider_name="custom_api_key",
            data_encrypted="gAAAAA_stub_encrypted",
        )
        db.add(cred)
        await db.commit()
        return cred.id


async def _seed_connection(
    user_id: uuid.UUID, credential_id: uuid.UUID | None, type_: str = "custom"
) -> uuid.UUID:
    async with TestSession() as db:
        conn = Connection(
            user_id=user_id,
            type=type_,
            provider_name="custom_api_key" if type_ == "custom" else "naver",
            display_name=f"{type_} conn",
            credential_id=credential_id,
            status="active",
        )
        db.add(conn)
        await db.commit()
        return conn.id


@pytest.mark.asyncio
async def test_create_custom_tool_with_connection_id_returns_connection_id(
    client: AsyncClient,
):
    """POST /api/tools/custom with a CUSTOM connection_id echoes it in response.

    Regression fence for M4: frontend find-or-create path passes connection_id
    (not credential_id). ToolResponse.connection_id must round-trip so the UI
    can render the bound connection chip.
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    conn_id = await _seed_connection(TEST_USER_ID, cred_id, type_="custom")

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My Custom Tool",
            "api_url": "https://api.example.com/v1/do",
            "http_method": "POST",
            "connection_id": str(conn_id),
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["connection_id"] == str(conn_id)
    assert data["type"] == "custom"


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_other_user_connection(client: AsyncClient):
    """Binding another user's connection returns 404 (IDOR guard)."""
    await _seed_user()
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    async with TestSession() as db:
        other = User(id=other_user_id, email="other@test.com", name="Other")
        db.add(other)
        await db.commit()

    cred_id = await _seed_custom_credential(other_user_id)
    foreign_conn_id = await _seed_connection(other_user_id, cred_id, type_="custom")

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Leaky Custom",
            "api_url": "https://api.example.com",
            "connection_id": str(foreign_conn_id),
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_non_custom_connection_type(
    client: AsyncClient,
):
    """Binding a PREBUILT-type connection to a CUSTOM tool returns 404.

    Type guard: chat_service._resolve_custom_auth is only wired into the CUSTOM
    branch. Allowing a PREBUILT connection on a CUSTOM tool would misroute to
    the PREBUILT resolver at runtime or fall into _resolve_legacy_tool_auth.
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    prebuilt_conn_id = await _seed_connection(
        TEST_USER_ID, cred_id, type_="prebuilt"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Misrouted Custom",
            "api_url": "https://api.example.com",
            "connection_id": str(prebuilt_conn_id),
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tools/custom — consistency + fail-closed guards
# (Codex adversarial 3차 [high] + [medium])
# ---------------------------------------------------------------------------


async def _seed_connection_with_status(
    user_id: uuid.UUID,
    credential_id: uuid.UUID | None,
    status: str,
    display_name: str = "test conn",
) -> uuid.UUID:
    async with TestSession() as db:
        conn = Connection(
            user_id=user_id,
            type="custom",
            provider_name="custom_api_key",
            display_name=display_name,
            credential_id=credential_id,
            status=status,
        )
        db.add(conn)
        await db.commit()
        return conn.id


@pytest.mark.asyncio
async def test_create_custom_tool_derives_credential_id_from_connection(
    client: AsyncClient,
):
    """클라이언트가 보낸 mismatched credential_id는 무시되고 server가
    conn.credential_id로 override해야 한다 (Codex adversarial 3차 [high]).
    split-brain 원천 차단: runtime bridge override는 오로지 PATCH /auth-config
    회전 케이스만 진입 가능.
    """
    await _seed_user()
    # connection에 바인딩된 credential
    cred_in_conn = await _seed_custom_credential(TEST_USER_ID)
    conn_id = await _seed_connection(TEST_USER_ID, cred_in_conn, type_="custom")
    # 서로 다른 credential (클라이언트가 실수 또는 악의로 전송)
    other_cred = await _seed_custom_credential(TEST_USER_ID)
    assert other_cred != cred_in_conn

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Consistent Tool",
            "api_url": "https://api.example.com",
            "credential_id": str(other_cred),  # 서버가 무시해야 함
            "connection_id": str(conn_id),
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["connection_id"] == str(conn_id)
    # 서버가 conn.credential_id로 override했어야 함 (other_cred가 아니라)
    assert data["credential_id"] == str(cred_in_conn), (
        "server must derive credential_id from connection to prevent "
        "split-brain state (tool.credential_id != tool.connection.credential_id)"
    )


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_disabled_connection(
    client: AsyncClient,
):
    """disabled connection에 새 tool을 바인딩하면 409 — runtime에서 fail-closed로
    어차피 막히지만 creation time에 latent failure 차단
    (Codex adversarial 3차 [medium]).
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    disabled_conn_id = await _seed_connection_with_status(
        TEST_USER_ID, cred_id, status="disabled", display_name="disabled"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Dead-on-arrival",
            "api_url": "https://api.example.com",
            "connection_id": str(disabled_conn_id),
        },
    )
    assert resp.status_code == 409
    detail = resp.json().get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("error_code") == "CUSTOM_CONNECTION_DISABLED"


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_connection_without_credential(
    client: AsyncClient,
):
    """credential이 바인딩되지 않은 connection에 tool을 바인딩하면 400
    (Codex adversarial 3차 [medium]). runtime `_resolve_custom_auth` State 5b
    `ToolConfigError` 로 막히지만 creation time에 차단.
    """
    await _seed_user()
    unbound_conn_id = await _seed_connection_with_status(
        TEST_USER_ID, None, status="active", display_name="unbound"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Unbound Tool",
            "api_url": "https://api.example.com",
            "connection_id": str(unbound_conn_id),
        },
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("error_code") == "CUSTOM_CONNECTION_UNBOUND"
