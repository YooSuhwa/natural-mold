"""Marketplace MCP server payload tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name
from app.credentials import service as credential_service
from app.exceptions import AppError
from app.marketplace.mcp_server import build_mcp_server_payload
from app.models.agent import Agent
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


def _server(**overrides) -> McpServer:
    data = {
        "id": uuid.uuid4(),
        "user_id": TEST_USER_ID,
        "name": "Atlassian MCP",
        "description": "Jira and Confluence tools",
        "transport": "streamable_http",
        "url": "https://mcp.atlassian.com/v1/mcp",
        "command": None,
        "args": [],
        "env_vars": {},
        "headers": {"Authorization": "=Bearer {{ $credentials.access_token }}"},
        "credential_id": uuid.uuid4(),
        "status": "connected",
        "last_tool_count": 1,
    }
    data.update(overrides)
    return McpServer(**data)


def test_build_mcp_server_payload_strips_runtime_and_credential_fields() -> None:
    payload = build_mcp_server_payload(
        _server(),
        credential_definition_key="mcp_oauth2",
        tool_snapshot=[
            {
                "name": "search_jira",
                "description": "Search Jira issues",
                "input_schema": {"type": "object"},
            }
        ],
    )

    assert payload["resource"] == "mcp_server"
    assert payload["transport"] == "streamable_http"
    assert payload["headers"] == {
        "Authorization": "=Bearer {{ $credentials.access_token }}"
    }
    assert payload["credential_definition_key"] == "mcp_oauth2"
    assert payload["tool_snapshot"][0]["name"] == "search_jira"
    assert "credential_id" not in payload
    assert "status" not in payload
    assert "last_tool_count" not in payload


def test_build_mcp_server_payload_rejects_secret_header_value() -> None:
    with pytest.raises(AppError) as exc:
        build_mcp_server_payload(
            _server(headers={"Authorization": "Bearer sk-123456789012345678901234"}),
            credential_definition_key="mcp_oauth2",
        )

    assert exc.value.code == "MARKETPLACE_SECRET_DETECTED"


def test_build_mcp_server_payload_marks_stdio_manual_only() -> None:
    payload = build_mcp_server_payload(
        _server(
            transport="stdio",
            url=None,
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
        ),
        credential_definition_key=None,
    )

    assert payload["security"]["stdio_risk"] is True
    assert payload["security"]["support_level"] == "manual_only"


async def _ensure_test_user(db: AsyncSession) -> None:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        db.add(
            User(
                id=TEST_USER_ID,
                email="test@test.com",
                name="Test User",
                hashed_password="h",
                is_active=True,
                is_super_user=True,
            )
        )
        await db.flush()


@pytest.mark.asyncio
async def test_publish_mcp_server_creates_item_version_and_publication_link(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian OAuth",
        data={
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 9999999999,
        },
    )
    server = _server(credential_id=credential.id)
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={
            "visibility": "public",
            "name": "Atlassian MCP",
            "description": "Jira and Confluence tools",
            "tags": ["atlassian"],
            "categories": ["productivity"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "mcp"
    assert body["status"] == "published"
    assert body["latest_version"] is not None

    item = await db.get(MarketplaceItem, uuid.UUID(body["id"]))
    assert item is not None
    assert item.owner_user_id == TEST_USER_ID
    assert item.latest_version_id is not None

    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    assert version.resource_type == "mcp"
    assert version.payload_kind == "mcp_template"
    assert version.storage_path is None
    assert version.payload["resource"] == "mcp_server"
    assert "credential_id" not in version.payload
    assert version.credential_requirements == [
        {
            "key": "mcp_auth",
            "definition_key": "mcp_oauth2",
            "required": True,
            "label": "MCP credential",
            "description": "Credential used to authenticate the MCP server",
            "fields": [],
            "injection": "config",
            "scope": "user",
        }
    ]

    link = (
        await db.execute(
            select(MarketplacePublicationLink).where(
                MarketplacePublicationLink.item_id == item.id
            )
        )
    ).scalar_one_or_none()
    assert link is not None
    assert link.resource_type == "mcp"
    assert link.source_mcp_server_id == server.id


@pytest.mark.asyncio
async def test_publish_mcp_server_rejects_unbound_credential_template(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(credential_id=None)
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "public", "name": "Unbound Atlassian MCP"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.parametrize("visibility", ["public", "unlisted"])
@pytest.mark.asyncio
async def test_publish_stdio_mcp_server_rejects_public_distribution(
    visibility: str,
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        credential_id=None,
        transport="stdio",
        url=None,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        headers={},
    )
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": visibility, "name": "Filesystem MCP"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_patch_stdio_mcp_item_rejects_public_visibility(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        credential_id=None,
        transport="stdio",
        url=None,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        headers={},
    )
    db.add(server)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "private", "name": "Private Filesystem MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    response = await client.patch(
        f"/api/marketplace/items/{item_id}",
        json={"visibility": "public"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_install_mcp_marketplace_item_creates_mcp_server_needing_setup(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian OAuth",
        data={
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=credential.id)
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Installable Atlassian MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )

    assert install_response.status_code == 201, install_response.text
    body = install_response.json()
    assert body["resource_type"] == "mcp"
    assert body["install_status"] == "needs_setup"
    assert body["installed_mcp_server_id"] is not None
    assert body["installed_mcp_server_id"] != str(source.id)

    installed = await db.get(McpServer, uuid.UUID(body["installed_mcp_server_id"]))
    assert installed is not None
    assert installed.user_id == TEST_USER_ID
    assert installed.name == "Atlassian MCP"
    assert installed.credential_id is None
    assert installed.headers == {
        "Authorization": "=Bearer {{ $credentials.access_token }}"
    }


@pytest.mark.asyncio
async def test_install_mcp_marketplace_item_with_binding_is_active(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Source OAuth",
        data={
            "access_token": "source-token",
            "refresh_token": "source-refresh",
            "expires_at": 9999999999,
        },
    )
    install_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Install OAuth",
        data={
            "access_token": "install-token",
            "refresh_token": "install-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=source_credential.id)
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Bound Atlassian MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )

    assert install_response.status_code == 201, install_response.text
    body = install_response.json()
    assert body["install_status"] == "active"

    installed = await db.get(McpServer, uuid.UUID(body["installed_mcp_server_id"]))
    assert installed is not None
    assert installed.credential_id == install_credential.id


@pytest.mark.asyncio
async def test_mcp_installation_summary_recomputes_missing_credential(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Source OAuth",
        data={
            "access_token": "source-token",
            "refresh_token": "source-refresh",
            "expires_at": 9999999999,
        },
    )
    install_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Install OAuth",
        data={
            "access_token": "install-token",
            "refresh_token": "install-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=source_credential.id)
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Credential Summary MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )
    assert install_response.status_code == 201, install_response.text
    assert install_response.json()["install_status"] == "active"

    installed = await db.get(
        McpServer,
        uuid.UUID(install_response.json()["installed_mcp_server_id"]),
    )
    assert installed is not None
    installed.credential_id = None
    await db.commit()

    detail_response = await client.get(f"/api/marketplace/items/{item_id}")

    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["installation"]["status"] == "needs_setup"
    list_response = await client.get("/api/marketplace/items?resource_type=mcp")
    assert list_response.status_code == 200, list_response.text
    listed = next(row for row in list_response.json() if row["id"] == item_id)
    assert listed["installation"]["status"] == "needs_setup"


@pytest.mark.asyncio
async def test_install_mcp_marketplace_item_materializes_tool_snapshot(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Source OAuth",
        data={
            "access_token": "source-token",
            "refresh_token": "source-refresh",
            "expires_at": 9999999999,
        },
    )
    install_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Install OAuth",
        data={
            "access_token": "install-token",
            "refresh_token": "install-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=source_credential.id)
    source_tool = McpTool(
        id=uuid.uuid4(),
        server_id=source.id,
        name="search_jira",
        description="Search Jira issues",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        enabled=True,
    )
    db.add_all([source, source_tool])
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Snapshot Atlassian MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )

    assert install_response.status_code == 201, install_response.text
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])
    installed_tools = (
        await db.execute(
            select(McpTool).where(McpTool.server_id == installed_server_id)
        )
    ).scalars().all()
    assert [(tool.name, tool.description) for tool in installed_tools] == [
        ("search_jira", "Search Jira issues")
    ]
    assert installed_tools[0].input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }


@pytest.mark.asyncio
async def test_update_mcp_installation_reconciles_removed_tool_snapshot(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source = _server(credential_id=None, name="Docs MCP", headers={})
    old_source_tool = McpTool(
        id=uuid.uuid4(),
        server_id=source.id,
        name="search_jira",
        description="Search Jira issues",
        input_schema={"type": "object"},
        enabled=True,
    )
    db.add_all([source, old_source_tool])
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Reconciled Docs MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["id"]
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])

    await db.delete(old_source_tool)
    db.add(
        McpTool(
            id=uuid.uuid4(),
            server_id=source.id,
            name="search_confluence",
            description="Search Confluence pages",
            input_schema={"type": "object"},
            enabled=True,
        )
    )
    await db.commit()

    publish_update_response = await client.post(
        f"/api/marketplace/items/{item_id}/versions/from-mcp/{source.id}",
        json={"release_notes": "Replace Jira with Confluence"},
    )
    assert publish_update_response.status_code == 200, publish_update_response.text

    update_response = await client.post(
        f"/api/marketplace/installations/{installation_id}/update",
        json={"strategy": "overwrite"},
    )

    assert update_response.status_code == 200, update_response.text
    installed = await db.get(McpServer, installed_server_id)
    assert installed is not None
    assert installed.last_tool_count == 1
    installed_tools = (
        await db.execute(
            select(McpTool)
            .where(McpTool.server_id == installed_server_id)
            .order_by(McpTool.name.asc())
        )
    ).scalars().all()
    # The snapshot is authoritative — the renamed-away ``search_jira`` row is
    # deleted (not left as a disabled dangling row), so the total tool count
    # matches the snapshot.
    assert [tool.name for tool in installed_tools] == ["search_confluence"]
    assert installed_tools[0].enabled is True
    assert len(installed_tools) == installed.last_tool_count


@pytest.mark.asyncio
async def test_install_mcp_reuse_or_update_applies_credential_binding(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Source OAuth",
        data={
            "access_token": "source-token",
            "refresh_token": "source-refresh",
            "expires_at": 9999999999,
        },
    )
    install_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Install OAuth",
        data={
            "access_token": "install-token",
            "refresh_token": "install-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=source_credential.id)
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Reusable Atlassian MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    initial_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert initial_response.status_code == 201, initial_response.text
    assert initial_response.json()["install_status"] == "needs_setup"
    installation_id = initial_response.json()["id"]
    server_id = uuid.UUID(initial_response.json()["installed_mcp_server_id"])

    retry_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "reuse_or_update",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )

    assert retry_response.status_code == 201, retry_response.text
    assert retry_response.json()["id"] == installation_id
    assert retry_response.json()["install_status"] == "active"

    installed = await db.get(McpServer, server_id)
    assert installed is not None
    assert installed.credential_id == install_credential.id


@pytest.mark.asyncio
async def test_install_mcp_reuse_or_update_preserves_manual_tool_toggle(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Credential-only ``reuse_or_update`` must not reset user tool toggles."""

    await _ensure_test_user(db)
    source_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Source OAuth",
        data={
            "access_token": "source-token",
            "refresh_token": "source-refresh",
            "expires_at": 9999999999,
        },
    )
    install_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Install OAuth",
        data={
            "access_token": "install-token",
            "refresh_token": "install-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=source_credential.id)
    db.add_all(
        [
            source,
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="search_jira",
                description="Search Jira issues",
                input_schema={"type": "object"},
                enabled=True,
            ),
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="create_issue",
                description="Create a Jira issue",
                input_schema={"type": "object"},
                enabled=True,
            ),
        ]
    )
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Toggle Atlassian MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    initial_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )
    assert initial_response.status_code == 201, initial_response.text
    server_id = uuid.UUID(initial_response.json()["installed_mcp_server_id"])

    # The user manually disables one of the installed tools.
    toggled = (
        await db.execute(
            select(McpTool).where(
                McpTool.server_id == server_id, McpTool.name == "create_issue"
            )
        )
    ).scalar_one()
    toggled.enabled = False
    await db.commit()

    retry_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "reuse_or_update",
            "credential_bindings": {"mcp_auth": str(install_credential.id)},
        },
    )
    assert retry_response.status_code == 201, retry_response.text

    tools = (
        await db.execute(select(McpTool).where(McpTool.server_id == server_id))
    ).scalars().all()
    enabled_by_name = {tool.name: tool.enabled for tool in tools}
    assert enabled_by_name == {"search_jira": True, "create_issue": False}


@pytest.mark.asyncio
async def test_publish_mcp_version_rejects_non_mcp_marketplace_item(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(credential_id=None)
    agent_item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="agent",
        owner_user_id=TEST_USER_ID,
        is_system=False,
        is_listed=True,
        name="Existing Agent Item",
        slug=f"existing-agent-{uuid.uuid4().hex[:8]}",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
    )
    db.add_all([server, agent_item])
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/{agent_item.id}/versions/from-mcp/{server.id}",
        json={"release_notes": "Should not attach MCP versions to agents"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_update_mcp_installation_overwrites_installed_server(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source = _server(
        credential_id=None,
        name="Docs MCP",
        url="https://mcp.example.test/v1",
        headers={},
    )
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Docs MCP Template"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["id"]
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])

    source.name = "Docs MCP Updated"
    source.url = "https://mcp.example.test/v2"
    await db.commit()
    publish_update_response = await client.post(
        f"/api/marketplace/items/{item_id}/versions/from-mcp/{source.id}",
        json={"release_notes": "Updated URL"},
    )
    assert publish_update_response.status_code == 200, publish_update_response.text

    update_response = await client.post(
        f"/api/marketplace/installations/{installation_id}/update",
        json={"strategy": "overwrite"},
    )

    assert update_response.status_code == 200, update_response.text
    installed = await db.get(McpServer, installed_server_id)
    assert installed is not None
    assert installed.name == "Docs MCP Updated"
    assert installed.url == "https://mcp.example.test/v2"


@pytest.mark.asyncio
async def test_delete_mcp_installation_with_resource_removes_server(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source = _server(credential_id=None, headers={})
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Disposable MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = uuid.UUID(install_response.json()["id"])
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])

    delete_response = await client.delete(
        f"/api/marketplace/installations/{installation_id}",
        params={"delete_resource": True},
    )

    assert delete_response.status_code == 204
    assert await db.get(McpServer, installed_server_id) is None
    assert await db.get(MarketplaceInstallation, installation_id) is None

@pytest.mark.asyncio
async def test_install_mcp_item_honors_enabled_tool_names_install_default(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """``install_defaults.enabled_tool_names`` selects which snapshot tools
    start enabled; tools outside the list materialize disabled."""

    await _ensure_test_user(db)
    source = _server(credential_id=None, headers={})
    db.add_all(
        [
            source,
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="search_jira",
                description="Search Jira issues",
                input_schema={"type": "object"},
                enabled=True,
            ),
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="search_confluence",
                description="Search Confluence pages",
                input_schema={"type": "object"},
                enabled=True,
            ),
        ]
    )
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Partial Default MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    item = await db.get(MarketplaceItem, uuid.UUID(item_id))
    assert item is not None and item.latest_version_id is not None
    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    payload = dict(version.payload)
    payload["install_defaults"] = {
        **(payload.get("install_defaults") or {}),
        "enabled_tool_names": ["search_jira"],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )

    assert install_response.status_code == 201, install_response.text
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])
    installed_tools = (
        await db.execute(
            select(McpTool)
            .where(McpTool.server_id == installed_server_id)
            .order_by(McpTool.name.asc())
        )
    ).scalars().all()
    assert [(tool.name, tool.enabled) for tool in installed_tools] == [
        ("search_confluence", False),
        ("search_jira", True),
    ]


@pytest.mark.asyncio
async def test_install_mcp_item_without_install_defaults_enables_all_snapshot_tools(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    source = _server(credential_id=None, headers={})
    db.add_all(
        [
            source,
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="search_jira",
                description="Search Jira issues",
                input_schema={"type": "object"},
                enabled=True,
            ),
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="search_confluence",
                description="Search Confluence pages",
                input_schema={"type": "object"},
                enabled=True,
            ),
        ]
    )
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Legacy Defaults MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    item = await db.get(MarketplaceItem, uuid.UUID(item_id))
    assert item is not None and item.latest_version_id is not None
    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    payload = dict(version.payload)
    payload.pop("install_defaults", None)
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )

    assert install_response.status_code == 201, install_response.text
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])
    installed_tools = (
        await db.execute(
            select(McpTool).where(McpTool.server_id == installed_server_id)
        )
    ).scalars().all()
    assert sorted((tool.name, tool.enabled) for tool in installed_tools) == [
        ("search_confluence", True),
        ("search_jira", True),
    ]


@pytest.mark.asyncio
async def test_install_mcp_reuse_or_update_rejects_foreign_server(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """reuse_or_update must re-validate server ownership before overwriting
    ``credential_id`` (collapsed to 404 per the enumeration convention)."""

    await _ensure_test_user(db)
    binding_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Binding OAuth",
        data={
            "access_token": "binding-token",
            "refresh_token": "binding-refresh",
            "expires_at": 9999999999,
        },
    )
    source = _server(credential_id=None, headers={})
    db.add(source)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Hijack Target MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = uuid.UUID(install_response.json()["id"])

    other_user = User(
        id=uuid.uuid4(),
        email="other-mcp@test.com",
        name="Other User",
        hashed_password="h",
        is_active=True,
        is_super_user=False,
    )
    foreign_server = _server(
        user_id=other_user.id, credential_id=None, headers={}, name="Foreign MCP"
    )
    db.add_all([other_user, foreign_server])
    installation = await db.get(MarketplaceInstallation, installation_id)
    assert installation is not None
    installation.installed_mcp_server_id = foreign_server.id
    await db.commit()

    retry_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={
            "install_mode": "reuse_or_update",
            "credential_bindings": {"mcp_auth": str(binding_credential.id)},
        },
    )

    assert retry_response.status_code == 404, retry_response.text
    assert retry_response.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"
    foreign = await db.get(McpServer, foreign_server.id)
    assert foreign is not None
    assert foreign.credential_id is None


@pytest.mark.asyncio
async def test_update_mcp_installation_deletes_renamed_tool_and_agent_link(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A tool renamed away in the new snapshot is removed from the DB and any
    agent link pointing at it is cleaned up (no dangling rows)."""

    await _ensure_test_user(db)
    source = _server(credential_id=None, name="Fetcher MCP", headers={})
    db.add_all(
        [
            source,
            McpTool(
                id=uuid.uuid4(),
                server_id=source.id,
                name="fetch",
                description="Fetch a URL",
                input_schema={"type": "object"},
                enabled=True,
            ),
        ]
    )
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Fetcher MCP"},
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    install_response = await client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["id"]
    installed_server_id = uuid.UUID(install_response.json()["installed_mcp_server_id"])

    installed_fetch = (
        await db.execute(
            select(McpTool).where(
                McpTool.server_id == installed_server_id, McpTool.name == "fetch"
            )
        )
    ).scalar_one()
    old_tool_id = installed_fetch.id

    # An agent links the installed ``fetch`` tool.
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    agent_id = uuid.uuid4()
    agent = Agent(
        id=agent_id,
        user_id=TEST_USER_ID,
        name="Fetcher Agent",
        system_prompt="Fetch things.",
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode="per_user",
        model_id=model.id,
        status="active",
    )
    db.add_all([model, agent])
    await db.flush()
    db.add(AgentMcpToolLink(agent_id=agent_id, mcp_tool_id=old_tool_id))
    await db.commit()

    # Publisher renames ``fetch`` -> ``fetch_v2`` and cuts a new version.
    renamed = (
        await db.execute(
            select(McpTool).where(
                McpTool.server_id == source.id, McpTool.name == "fetch"
            )
        )
    ).scalar_one()
    renamed.name = "fetch_v2"
    await db.commit()

    publish_update_response = await client.post(
        f"/api/marketplace/items/{item_id}/versions/from-mcp/{source.id}",
        json={"release_notes": "Rename fetch to fetch_v2"},
    )
    assert publish_update_response.status_code == 200, publish_update_response.text

    update_response = await client.post(
        f"/api/marketplace/installations/{installation_id}/update",
        json={"strategy": "overwrite"},
    )
    assert update_response.status_code == 200, update_response.text

    # The update ran in the request's own session — drop identity-map state
    # so the assertions read committed rows, not stale cached objects.
    db.expire_all()
    installed_tools = (
        await db.execute(select(McpTool).where(McpTool.server_id == installed_server_id))
    ).scalars().all()
    assert [tool.name for tool in installed_tools] == ["fetch_v2"]
    installed_server = await db.get(McpServer, installed_server_id)
    assert installed_server is not None
    assert installed_server.last_tool_count == len(installed_tools)
    # Old row and its agent link are gone.
    assert await db.get(McpTool, old_tool_id) is None
    assert await db.get(AgentMcpToolLink, (agent_id, old_tool_id)) is None
