"""Tests for the curated MCP server registry + ``/from-registry`` route."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.user import User
from app.services import mcp_registry
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="reg@test", name="reg"))
        await db.commit()


# ---------------------------------------------------------------------------
# Registry service
# ---------------------------------------------------------------------------


def test_list_registry_exposes_all_curated_entries() -> None:
    entries = mcp_registry.list_registry()
    keys = {e["key"] for e in entries}
    assert {"github", "linear", "jira", "slack", "notion"}.issubset(keys)
    assert {
        "hancom-gw",
        "hancom-mile",
        "hancom-org-chart",
        "maepsi",
    }.issubset(keys)
    assert len(entries) >= 9


def test_get_registry_entry_returns_full_payload() -> None:
    entry = mcp_registry.get_registry_entry("github")
    assert entry is not None
    assert entry["key"] == "github"
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://api.githubcopilot.com/mcp/"
    assert entry["credential_definition_key"] == "http_bearer"


def test_get_registry_entry_unknown_returns_none() -> None:
    assert mcp_registry.get_registry_entry("definitely-not-a-real-server") is None


def test_atlassian_rovo_registry_uses_mcp_oauth2() -> None:
    entry = mcp_registry.get_registry_entry("atlassian-rovo")
    assert entry is not None
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://mcp.atlassian.com/v1/mcp/authv2"
    assert entry["credential_definition_key"] == "mcp_oauth2"


def test_jira_registry_alias_uses_atlassian_rovo_oauth2() -> None:
    entry = mcp_registry.get_registry_entry("jira")
    assert entry is not None
    assert entry["url"] == "https://mcp.atlassian.com/v1/mcp/authv2"
    assert entry["credential_definition_key"] == "mcp_oauth2"


def test_stdio_entries_carry_command_and_args() -> None:
    slack = mcp_registry.get_registry_entry("slack")
    assert slack is not None
    assert slack["transport"] == "stdio"
    assert slack["command"] == "npx"
    assert slack["args"] and slack["args"][0] == "-y"
    # env_vars use the ``${credential.<field>}`` template syntax.
    assert any("${credential." in v for v in slack["env_vars"].values())


def test_local_first_party_entries_use_mcp_secret_where_needed() -> None:
    gw = mcp_registry.get_registry_entry("hancom-gw")
    mile = mcp_registry.get_registry_entry("hancom-mile")
    org = mcp_registry.get_registry_entry("hancom-org-chart")
    maepsi = mcp_registry.get_registry_entry("maepsi")

    assert gw is not None
    assert gw["url"] == "http://localhost:18003/mcp"
    assert gw["credential_definition_key"] == "mcp_secret"
    assert mile is not None
    assert mile["url"] == "http://localhost:18004/mcp"
    assert mile["credential_definition_key"] == "mcp_secret"
    assert maepsi is not None
    assert maepsi["url"] == "http://localhost:18001/mcp/"
    assert maepsi["credential_definition_key"] == "mcp_secret"
    assert org is not None
    assert org["url"] == "http://localhost:18002/mcp"
    assert org["credential_definition_key"] is None


# ---------------------------------------------------------------------------
# Catalog router: GET /api/mcp-server-types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_lists_registry_entries(client: AsyncClient) -> None:
    response = await client.get("/api/mcp-server-types")
    assert response.status_code == 200, response.text
    body = response.json()
    keys = {e["key"] for e in body}
    assert {
        "github",
        "linear",
        "jira",
        "slack",
        "notion",
        "hancom-gw",
        "hancom-mile",
        "hancom-org-chart",
        "maepsi",
    }.issubset(keys)


@pytest.mark.asyncio
async def test_router_get_single_registry_entry(client: AsyncClient) -> None:
    response = await client.get("/api/mcp-server-types/notion")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == "notion"
    assert body["transport"] == "stdio"
    assert body["command"] == "npx"


@pytest.mark.asyncio
async def test_router_unknown_registry_entry_returns_404(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/mcp-server-types/nope")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Router: POST /api/mcp-servers/from-registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_registry_streamable_http(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(
        "/api/mcp-servers/from-registry",
        json={"registry_key": "github", "name": "My GitHub"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transport"] == "streamable_http"
    assert body["url"] == "https://api.githubcopilot.com/mcp/"
    assert body["name"] == "My GitHub"
    assert body["credential_id"] is None

    server_id = uuid.UUID(body["id"])
    row = (await db.execute(select(McpServer).where(McpServer.id == server_id))).scalar_one()
    assert row.transport == "streamable_http"
    assert row.url == "https://api.githubcopilot.com/mcp/"


@pytest.mark.asyncio
async def test_create_from_registry_stdio_carries_command_and_env(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post(
        "/api/mcp-servers/from-registry",
        json={"registry_key": "slack", "name": "Team Slack"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transport"] == "stdio"
    assert body["command"] == "npx"
    assert body["args"][0] == "-y"
    assert "SLACK_BOT_TOKEN" in body["env_vars"]


@pytest.mark.asyncio
async def test_create_from_registry_with_credential(client: AsyncClient, db: AsyncSession) -> None:
    """``credential_id`` is wired through verbatim (FK validity checked at PG)."""

    # Create a credential first via the credentials API so the FK lands valid.
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "http_bearer",
            "name": "github-token",
            "data": {"token": "ghp-x"},
        },
    )
    assert create.status_code == 201
    cred_id = create.json()["id"]

    response = await client.post(
        "/api/mcp-servers/from-registry",
        json={
            "registry_key": "github",
            "name": "My GitHub",
            "credential_id": cred_id,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["credential_id"] == cred_id


@pytest.mark.asyncio
async def test_create_from_registry_rejects_system_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
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

    response = await client.post(
        "/api/mcp-servers/from-registry",
        json={
            "registry_key": "github",
            "name": "My GitHub",
            "credential_id": str(credential.id),
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_from_registry_unknown_key_returns_400(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/mcp-servers/from-registry",
        json={"registry_key": "fictional-server", "name": "n"},
    )
    assert response.status_code == 400
    assert "fictional-server" in response.json()["error"]["message"]
