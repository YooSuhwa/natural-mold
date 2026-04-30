"""Tests for the MCP server / tool API (M3).

The MCP Python SDK does live network IO — patch
:func:`app.mcp.client.connect_and_list` so each test pins a synthetic probe
result. This keeps the suite hermetic while still exercising the router,
discovery upsert, env_vars interpolation, and status transitions.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.mcp import client as mcp_client
from app.mcp import discovery as mcp_discovery
from app.mcp.client import build_env_vars, build_headers
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


def _patch_probe(monkeypatch, payload: dict[str, Any]) -> None:
    """Force both ``client`` and ``discovery`` modules to use the stub."""

    async def _stub(**_: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(mcp_client, "connect_and_list", _stub)
    monkeypatch.setattr(mcp_discovery, "connect_and_list", _stub)


# -- CRUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_mcp_server(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(
        "/api/mcp-servers",
        json={
            "name": "Demo",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": {"X-Trace": "1"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    server_id = uuid.UUID(body["id"])
    assert body["status"] == "unknown"
    assert body["transport"] == "streamable_http"

    row = (
        await db.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one()
    assert row.url == "https://mcp.example.com"
    assert row.headers == {"X-Trace": "1"}


@pytest.mark.asyncio
async def test_create_mcp_server_invalid_transport_combo(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/mcp-servers",
        json={"name": "no url", "transport": "streamable_http"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_mcp_server_with_tools(
    client: AsyncClient, db: AsyncSession
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="A",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.flush()
    db.add(
        McpTool(
            server_id=server.id,
            name="echo",
            description="echoes",
            input_schema={"type": "object"},
        )
    )
    await db.commit()
    response = await client.get(f"/api/mcp-servers/{server.id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["tools"]) == 1
    assert body["tools"][0]["name"] == "echo"


@pytest.mark.asyncio
async def test_patch_mcp_server(client: AsyncClient, db: AsyncSession) -> None:
    create = await client.post(
        "/api/mcp-servers",
        json={
            "name": "old",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
        },
    )
    sid = create.json()["id"]
    patch = await client.patch(
        f"/api/mcp-servers/{sid}",
        json={"name": "new", "headers": {"X-A": "1"}},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["name"] == "new"
    assert body["headers"] == {"X-A": "1"}


@pytest.mark.asyncio
async def test_delete_mcp_server_cascades_tools(
    client: AsyncClient, db: AsyncSession
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="X",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.flush()
    db.add(McpTool(server_id=server.id, name="t1"))
    await db.commit()
    sid = server.id

    response = await client.delete(f"/api/mcp-servers/{sid}")
    assert response.status_code == 204

    remaining_tools = (
        await db.execute(select(McpTool).where(McpTool.server_id == sid))
    ).scalars().all()
    assert remaining_tools == []
    remaining_servers = (
        await db.execute(select(McpServer).where(McpServer.id == sid))
    ).scalar_one_or_none()
    assert remaining_servers is None


# -- Connectivity probes -----------------------------------------------------


@pytest.mark.asyncio
async def test_test_endpoint_marks_connected(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Hello",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.commit()

    _patch_probe(
        monkeypatch,
        {
            "success": True,
            "server_info": {"name": "demo", "version": "1.0"},
            "tools": [
                {"name": "echo", "description": "", "input_schema": {}},
                {"name": "add", "description": "", "input_schema": {}},
            ],
            "error": None,
        },
    )

    response = await client.post(f"/api/mcp-servers/{server.id}/test")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "connected"
    assert body["tool_count"] == 2

    await db.refresh(server)
    assert server.status == "connected"
    assert server.last_tool_count == 2
    assert server.last_pinged_at is not None


@pytest.mark.asyncio
async def test_test_endpoint_marks_auth_needed_on_401(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Auth",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.commit()

    _patch_probe(
        monkeypatch,
        {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "HTTP 401 Unauthorized",
        },
    )

    response = await client.post(f"/api/mcp-servers/{server.id}/test")
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "auth_needed"


@pytest.mark.asyncio
async def test_discover_persists_tools(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Disc",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.commit()
    sid = server.id

    _patch_probe(
        monkeypatch,
        {
            "success": True,
            "server_info": {},
            "tools": [
                {
                    "name": "alpha",
                    "description": "alpha tool",
                    "input_schema": {"type": "object"},
                },
                {
                    "name": "beta",
                    "description": "beta tool",
                    "input_schema": {"type": "object"},
                },
            ],
            "error": None,
        },
    )

    response = await client.post(f"/api/mcp-servers/{sid}/discover")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert sorted(t["name"] for t in body["tools"]) == ["alpha", "beta"]

    rows = (
        await db.execute(select(McpTool).where(McpTool.server_id == sid))
    ).scalars().all()
    assert {r.name for r in rows} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_discover_drops_stale_tools(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Stale",
        transport="streamable_http",
        url="https://mcp.example.com",
    )
    db.add(server)
    await db.flush()
    db.add(McpTool(server_id=server.id, name="old"))
    await db.commit()
    sid = server.id

    _patch_probe(
        monkeypatch,
        {
            "success": True,
            "server_info": {},
            "tools": [{"name": "fresh", "description": "", "input_schema": {}}],
            "error": None,
        },
    )

    await client.post(f"/api/mcp-servers/{sid}/discover")

    rows = (
        await db.execute(select(McpTool).where(McpTool.server_id == sid))
    ).scalars().all()
    assert {r.name for r in rows} == {"fresh"}


# -- Interpolation -----------------------------------------------------------


def test_build_headers_resolves_credential_template() -> None:
    out = build_headers(
        {"Authorization": "=Bearer {{ $credentials.token }}"},
        {"token": "xyz"},
    )
    assert out == {"Authorization": "Bearer xyz"}


def test_build_env_vars_resolves_credential_template() -> None:
    out = build_env_vars(
        {
            "OPENAI_API_KEY": "={{ $credentials.api_key }}",
            "STATIC": "constant",
        },
        {"api_key": "sk-secret"},
    )
    assert out == {"OPENAI_API_KEY": "sk-secret", "STATIC": "constant"}


def test_build_headers_skips_when_empty() -> None:
    assert build_headers(None, None) == {}
    assert build_headers({}, None) == {}


@pytest.mark.asyncio
async def test_discovery_uses_credential_payload_for_headers(
    db: AsyncSession, monkeypatch
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="http_bearer",
        name="mcp-bearer",
        data={"token": "T-123"},
    )
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Auth-MCP",
        transport="streamable_http",
        url="https://mcp.example.com",
        headers={"Authorization": "=Bearer {{ $credentials.token }}"},
        credential_id=cred.id,
    )
    db.add(server)
    await db.commit()

    captured: dict[str, Any] = {}

    async def _stub(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "server_info": {},
            "tools": [],
            "error": None,
        }

    monkeypatch.setattr(mcp_client, "connect_and_list", _stub)
    monkeypatch.setattr(mcp_discovery, "connect_and_list", _stub)

    await mcp_discovery.test_server(db, server)
    assert captured["headers"] == {
        "Authorization": "=Bearer {{ $credentials.token }}"
    }
    # The credential payload was decrypted and forwarded to connect_and_list.
    assert captured["credentials"] == {"token": "T-123"}
