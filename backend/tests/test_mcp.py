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
from app.credentials.registry import registry
from app.mcp import client as mcp_client
from app.mcp import discovery as mcp_discovery
from app.mcp.client import build_env_vars, build_headers
from app.models.credential import Credential
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

    row = (await db.execute(select(McpServer).where(McpServer.id == server_id))).scalar_one()
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
async def test_create_mcp_server_rejects_cross_user_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    other_user_id = uuid.uuid4()
    db.add(User(id=other_user_id, email="other-mcp@test.com", name="Other"))
    blob, key_id, field_keys = credential_service.encrypt_data({"token": "secret"})
    credential = Credential(
        user_id=other_user_id,
        definition_key="http_bearer",
        name="other-token",
        data_encrypted=blob,
        key_id=key_id,
        field_keys=field_keys,
    )
    db.add(credential)
    await db.commit()

    response = await client.post(
        "/api/mcp-servers",
        json={
            "name": "bad credential",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "credential_id": str(credential.id),
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_mcp_server_rejects_system_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    create = await client.post(
        "/api/mcp-servers",
        json={
            "name": "owned",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
        },
    )
    assert create.status_code == 201
    blob, key_id, field_keys = credential_service.encrypt_data({"token": "system"})
    credential = Credential(
        user_id=None,
        definition_key="http_bearer",
        name="system-token",
        data_encrypted=blob,
        key_id=key_id,
        field_keys=field_keys,
        is_system=True,
    )
    db.add(credential)
    await db.commit()

    response = await client.patch(
        f"/api/mcp-servers/{create.json()['id']}",
        json={"credential_id": str(credential.id)},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_mcp_server_with_tools(client: AsyncClient, db: AsyncSession) -> None:
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
async def test_delete_mcp_server_cascades_tools(client: AsyncClient, db: AsyncSession) -> None:
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
        (await db.execute(select(McpTool).where(McpTool.server_id == sid))).scalars().all()
    )
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
async def test_discover_persists_tools(client: AsyncClient, db: AsyncSession, monkeypatch) -> None:
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

    rows = (await db.execute(select(McpTool).where(McpTool.server_id == sid))).scalars().all()
    assert {r.name for r in rows} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_discover_preserves_stale_tools_and_marks_last_seen(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    """Stale tools are kept (M26) so agent_mcp_tools links don't dangle.

    Fresh tools get ``last_seen_at`` populated; stale rows keep their
    pre-existing (or NULL) timestamp so the UI can flag them.
    """

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

    rows = (await db.execute(select(McpTool).where(McpTool.server_id == sid))).scalars().all()
    by_name = {r.name: r for r in rows}
    # Both rows are still present — the stale "old" one survives so any
    # agent_mcp_tools link to it isn't broken.
    assert set(by_name) == {"fresh", "old"}
    assert by_name["fresh"].last_seen_at is not None
    assert by_name["old"].last_seen_at is None


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


# -- stdio transport ---------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_and_list_stdio_invokes_sdk(monkeypatch) -> None:
    """``transport='stdio'`` must run through ``stdio_client`` instead of
    returning the legacy "unsupported" error."""

    captured: dict[str, Any] = {}

    class _StubInit:
        serverInfo = type("SI", (), {"name": "stub", "version": "0.0.1"})()

    class _StubTool:
        name = "ping"
        description = "ping"
        inputSchema = {"type": "object"}

    class _StubTools:
        tools = [_StubTool()]

    class _StubSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a) -> None:
            return None

        async def initialize(self):
            return _StubInit()

        async def list_tools(self):
            return _StubTools()

    class _StubStdioCtx:
        def __init__(self, params) -> None:
            captured["params"] = params

        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, *_a) -> None:
            return None

    def _stub_stdio_client(params):
        return _StubStdioCtx(params)

    # Monkey-patch via the lazy import path used inside _connect_stdio.
    import mcp.client.session as _session_mod
    import mcp.client.stdio as _stdio_mod

    monkeypatch.setattr(_stdio_mod, "stdio_client", _stub_stdio_client)
    monkeypatch.setattr(_session_mod, "ClientSession", _StubSession)

    result = await mcp_client.connect_and_list(
        transport="stdio",
        url=None,
        command="echo",
        args=["hi"],
        env_vars={"FOO": "bar"},
    )

    assert result["success"] is True
    assert result["tools"] == [
        {"name": "ping", "description": "ping", "input_schema": {"type": "object"}}
    ]
    # StdioServerParameters carries the resolved command/args/env so the SDK
    # spawns the right child process.
    assert captured["params"].command == "echo"
    assert captured["params"].args == ["hi"]
    assert captured["params"].env == {"FOO": "bar"}


@pytest.mark.asyncio
async def test_connect_and_list_stdio_requires_command() -> None:
    result = await mcp_client.connect_and_list(transport="stdio", url=None, command=None)
    assert result["success"] is False
    assert "command" in (result["error"] or "").lower()


# -- Import / Export ---------------------------------------------------------


@pytest.mark.asyncio
async def test_import_mcp_servers_creates_and_skips(client: AsyncClient, db: AsyncSession) -> None:
    # Pre-existing server with the same name to test skip vs overwrite.
    existing = McpServer(
        user_id=TEST_USER_ID,
        name="dup",
        transport="streamable_http",
        url="https://old.example.com",
    )
    db.add(existing)
    await db.commit()

    payload = {
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                "env": {"FOO": "bar"},
            },
            "supabase": {
                "transport": "streamable_http",
                "url": "https://supabase.example.com/mcp",
                "headers": {"Authorization": "Bearer X"},
            },
            "dup": {
                "transport": "streamable_http",
                "url": "https://new.example.com",
            },
            "broken": {
                # No command, no transport — should be reported as an error.
                "args": ["x"],
            },
        },
        "overwrite": False,
    }
    response = await client.post("/api/mcp-servers/import", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 2  # filesystem + supabase
    assert body["updated"] == 0
    assert body["skipped"] == 1  # dup
    assert any(e["name"] == "broken" for e in body["errors"])

    # Filesystem entry was inferred to stdio.
    rows = (
        (await db.execute(select(McpServer).where(McpServer.user_id == TEST_USER_ID)))
        .scalars()
        .all()
    )
    by_name = {r.name: r for r in rows}
    assert by_name["filesystem"].transport == "stdio"
    assert by_name["filesystem"].command == "npx"
    assert by_name["filesystem"].env_vars == {"FOO": "bar"}
    # Existing dup row was NOT overwritten.
    assert by_name["dup"].url == "https://old.example.com"


@pytest.mark.asyncio
async def test_import_mcp_servers_overwrite_updates_in_place(
    client: AsyncClient, db: AsyncSession
) -> None:
    existing = McpServer(
        user_id=TEST_USER_ID,
        name="dup",
        transport="streamable_http",
        url="https://old.example.com",
    )
    db.add(existing)
    await db.commit()
    original_id = existing.id

    payload = {
        "mcpServers": {
            "dup": {
                "transport": "streamable_http",
                "url": "https://new.example.com",
                "description": "updated",
            }
        },
        "overwrite": True,
    }
    response = await client.post("/api/mcp-servers/import", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    assert body["created"] == 0

    await db.refresh(existing)
    assert existing.id == original_id  # row preserved → tool links survive
    assert existing.url == "https://new.example.com"
    assert existing.description == "updated"


@pytest.mark.asyncio
async def test_export_mcp_servers_omits_secrets(client: AsyncClient, db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="http_bearer",
        name="export-bearer",
        data={"token": "SECRET"},
    )
    server = McpServer(
        user_id=TEST_USER_ID,
        name="exp",
        transport="streamable_http",
        url="https://exp.example.com",
        headers={"Authorization": "=Bearer {{ $credentials.token }}"},
        credential_id=cred.id,
    )
    db.add(server)
    await db.commit()

    response = await client.get("/api/mcp-servers/export")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "exp" in payload["mcpServers"]
    entry = payload["mcpServers"]["exp"]
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://exp.example.com"
    # Headers preserve the template (NOT the resolved bearer value).
    assert entry["headers"]["Authorization"] == "=Bearer {{ $credentials.token }}"
    # credential_id is exposed; the secret payload itself is never serialized.
    assert entry["credential_id"] == str(cred.id)
    assert "SECRET" not in response.text


# -- Health polling job ------------------------------------------------------


@pytest.mark.asyncio
async def test_register_mcp_health_job_skips_when_scheduler_idle() -> None:
    """The job is idempotent and a no-op when the scheduler isn't running."""

    from app.scheduler import MCP_HEALTH_JOB_ID, get_scheduler, register_mcp_health_job

    scheduler = get_scheduler()
    assert not scheduler.running
    register_mcp_health_job()
    assert scheduler.get_job(MCP_HEALTH_JOB_ID) is None


@pytest.mark.asyncio
async def test_poll_mcp_servers_health_updates_columns(db: AsyncSession, monkeypatch) -> None:
    """One sweep populates ``health_status`` / ``health_polled_at`` for every
    enabled server, regardless of probe success."""

    from app.scheduler import poll_mcp_servers_health

    ok_server = McpServer(
        user_id=TEST_USER_ID,
        name="ok",
        transport="streamable_http",
        url="https://ok.example.com",
    )
    bad_server = McpServer(
        user_id=TEST_USER_ID,
        name="bad",
        transport="streamable_http",
        url="https://bad.example.com",
    )
    db.add_all([ok_server, bad_server])
    await db.commit()
    ok_id = ok_server.id
    bad_id = bad_server.id

    async def _stub_test_server(_db, server):
        if server.name == "ok":
            return {"success": True, "tools": [], "server_info": {}, "error": None}
        return {
            "success": False,
            "tools": [],
            "server_info": {},
            "error": "boom",
        }

    from app.mcp import discovery as mcp_discovery

    monkeypatch.setattr(mcp_discovery, "test_server", _stub_test_server)

    # The job opens its own session via ``async_session()`` — point that at
    # the test's in-memory engine.
    from app import scheduler as scheduler_module
    from tests.conftest import TestSession

    monkeypatch.setattr(scheduler_module, "async_session", TestSession)

    counters = await poll_mcp_servers_health()
    assert counters["checked"] == 2
    assert counters["ok"] == 1
    assert counters["error"] == 1

    # Re-fetch via a fresh session — the job committed its own transaction.
    async with TestSession() as fresh:
        ok_row = await fresh.get(McpServer, ok_id)
        bad_row = await fresh.get(McpServer, bad_id)
        assert ok_row is not None and ok_row.health_status == "ok"
        assert ok_row.health_polled_at is not None
        assert bad_row is not None and bad_row.health_status == "error"
        assert bad_row.health_message == "boom"


# -- Skill prompt ------------------------------------------------------------


def test_build_skills_prompt_renders_block() -> None:
    from app.skills.prompt import build_skills_prompt

    out = build_skills_prompt(
        [
            {"name": "Slides", "slug": "slides", "description": "make decks"},
            {"name": "PDF", "slug": "pdf"},
        ]
    )
    assert "## Available Skills" in out
    assert "**Slides**: make decks" in out
    assert "/skills/slides/SKILL.md" in out
    # Missing description falls back to the placeholder so the line is never empty.
    assert "**PDF**: (no description)" in out


def test_build_skills_prompt_empty_returns_blank() -> None:
    from app.skills.prompt import build_skills_prompt

    assert build_skills_prompt([]) == ""
    assert build_skills_prompt([None, None]) == ""  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_discovery_uses_credential_payload_for_headers(db: AsyncSession, monkeypatch) -> None:
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
    assert captured["headers"] == {"Authorization": "Bearer T-123"}
    # The credential payload was decrypted and forwarded to connect_and_list.
    assert captured["credentials"] == {"token": "T-123"}


@pytest.mark.asyncio
async def test_test_endpoint_marks_auth_needed_when_oauth_refresh_fails(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="expired",
        data={
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": 1,
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Expired OAuth MCP",
        transport="streamable_http",
        url="https://mcp.example.com",
        credential_id=cred.id,
    )
    db.add(server)
    await db.commit()

    async def fail_refresh(_credentials: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("refresh token revoked")

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fail_refresh)

    response = await client.post(f"/api/mcp-servers/{server.id}/test")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "auth_needed"
    assert "refresh token revoked" in body["error"]


@pytest.mark.asyncio
async def test_probe_endpoint_returns_soft_failure_when_oauth_refresh_fails(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="expired",
        data={
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": 1,
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    await db.commit()

    async def fail_refresh(_credentials: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("refresh token revoked")

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fail_refresh)

    response = await client.post(
        "/api/mcp-servers/probe",
        json={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "credential_id": str(cred.id),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["tools"] == []
    assert "refresh token revoked" in body["error"]
