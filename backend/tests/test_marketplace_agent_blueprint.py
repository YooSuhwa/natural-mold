"""Marketplace Agent Blueprint install tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name
from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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


async def _make_agent_item(db: AsyncSession) -> tuple[MarketplaceItem, MarketplaceVersion]:
    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="agent",
        owner_user_id=TEST_USER_ID,
        is_system=False,
        is_listed=True,
        name="Research Agent",
        slug=f"research-agent-{uuid.uuid4().hex[:8]}",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
        published_at=_now(),
    )
    db.add(item)
    await db.flush()
    payload = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "agent": {
            "name": "Research Agent",
            "description": "Researches topics",
            "system_prompt": "You research topics.",
            "model": {"provider": "openai", "model_name": "gpt-5-mini"},
        },
        "capabilities": {"tools": [], "skills": [], "mcp_tools": [], "subagents": []},
        "setup": {"required_credentials": [], "warnings": [], "blocked_dependencies": []},
    }
    version = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="agent-1",
        version_number=1,
        resource_type="agent",
        payload_kind="agent_spec",
        payload=payload,
        storage_path=None,
        content_hash="b" * 64,
        size_bytes=1024,
        credential_requirements=[],
        dependency_requirements=[],
        execution_profile={},
        created_by=TEST_USER_ID,
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    await db.flush()
    return item, version


async def _make_agent_item_with_dependencies(
    db: AsyncSession,
) -> tuple[MarketplaceItem, MarketplaceVersion]:
    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="agent",
        owner_user_id=TEST_USER_ID,
        is_system=False,
        is_listed=True,
        name="Research Agent With Dependencies",
        slug=f"research-agent-deps-{uuid.uuid4().hex[:8]}",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
        published_at=_now(),
    )
    db.add(item)
    await db.flush()
    payload = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "agent": {
            "name": "Research Agent With Dependencies",
            "description": "Researches with dependencies",
            "system_prompt": "Use every linked capability.",
            "model": {"provider": "openai", "model_name": "gpt-5-mini"},
        },
        "capabilities": {
            "tools": [
                {
                    "name": "Current DateTime",
                    "definition_key": "builtin:current_datetime",
                    "parameters": {},
                }
            ],
            "skills": [
                {
                    "name": "Research Skill",
                    "slug": "research-skill",
                    "kind": "text",
                }
            ],
            "mcp_tools": [
                {
                    "name": "search_docs",
                    "description": "Search docs",
                    "input_schema": {"type": "object"},
                    "server": {
                        "name": "Docs MCP",
                        "description": "Documentation lookup",
                        "transport": "streamable_http",
                        "url": "https://mcp.example.test/mcp",
                        "command": None,
                        "args": [],
                        "env_vars": {},
                        "headers": {
                            "Authorization": "={{ $credentials.access_token }}"
                        },
                    },
                }
            ],
            "subagents": [],
        },
        "setup": {"required_credentials": [], "warnings": [], "blocked_dependencies": []},
    }
    version = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="agent-1",
        version_number=1,
        resource_type="agent",
        payload_kind="agent_spec",
        payload=payload,
        storage_path=None,
        content_hash="c" * 64,
        size_bytes=2048,
        credential_requirements=[],
        dependency_requirements=[],
        execution_profile={},
        created_by=TEST_USER_ID,
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    await db.flush()
    return item, version


@pytest.mark.asyncio
async def test_install_agent_marketplace_item_creates_agent_blueprint_not_agent(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, version = await _make_agent_item(db)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy", "name_override": "My Research Blueprint"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "agent"
    assert body["installed_agent_id"] is None
    assert body["installed_agent_blueprint_id"] is not None

    blueprint = await db.get(
        AgentBlueprint,
        uuid.UUID(body["installed_agent_blueprint_id"]),
    )
    assert blueprint is not None
    assert blueprint.user_id == TEST_USER_ID
    assert blueprint.name == "My Research Blueprint"
    assert blueprint.spec == version.payload
    assert blueprint.source_marketplace_item_id == item.id
    assert blueprint.source_marketplace_version_id == version.id

    detail_response = await client.get(f"/api/marketplace/items/{item.id}")
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["installation"]["installed"] is True
    assert detail["installation"]["installed_resource_id"] == str(blueprint.id)

    list_response = await client.get("/api/agent-blueprints")
    assert list_response.status_code == 200, list_response.text
    blueprints = list_response.json()
    assert [row["id"] for row in blueprints] == [str(blueprint.id)]
    assert blueprints[0]["name"] == "My Research Blueprint"
    assert blueprints[0]["installation_id"] == body["id"]

    get_response = await client.get(f"/api/agent-blueprints/{blueprint.id}")
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["spec"] == version.payload


@pytest.mark.asyncio
async def test_create_agent_from_installed_blueprint_materializes_runnable_agent(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={
            "name": "My Runnable Research Agent",
            "model_id": str(model.id),
        },
    )

    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["name"] == "My Runnable Research Agent"
    assert body["system_prompt"] == "You research topics."
    assert body["model"]["id"] == str(model.id)

    agent = await db.get(Agent, uuid.UUID(body["id"]))
    assert agent is not None
    assert agent.user_id == TEST_USER_ID
    assert agent.name == "My Runnable Research Agent"

    blueprint = await db.get(AgentBlueprint, uuid.UUID(blueprint_id))
    assert blueprint is not None
    assert blueprint.created_agent_count == 1


@pytest.mark.asyncio
async def test_create_agent_from_uninstalled_blueprint_is_blocked(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A soft-uninstalled blueprint is hidden from list/detail — create-agent
    must agree (404) so the gallery and the action stay consistent.
    """

    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]
    installation_id = install_response.json()["id"]

    uninstall_response = await client.delete(
        f"/api/marketplace/installations/{installation_id}"
    )
    assert uninstall_response.status_code in (200, 204), uninstall_response.text

    # Detail GET is hidden (404)…
    detail_response = await client.get(f"/api/agent-blueprints/{blueprint_id}")
    assert detail_response.status_code == 404, detail_response.text

    # …and create-agent must agree rather than succeed.
    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={"name": "Should Not Exist", "model_id": str(model.id)},
    )
    assert create_response.status_code == 404, create_response.text


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_resolves_model_from_spec(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["name"] == "Research Agent"
    assert body["model"]["id"] == str(model.id)


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_resolves_fallback_models_from_spec(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    primary = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    fallback = Model(
        id=uuid.uuid4(),
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        is_default=False,
        is_visible=True,
    )
    db.add_all([primary, fallback])
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    agent_spec = dict(payload["agent"])
    agent_spec["model_fallbacks"] = [
        {
            "provider": "anthropic",
            "model_name": "claude-sonnet-4-5",
            "display_name": "Claude Sonnet 4.5",
            "base_url": None,
        }
    ]
    payload["agent"] = agent_spec
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["model"]["id"] == str(primary.id)
    assert body["model_fallback_ids"] == [str(fallback.id)]


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_materializes_declared_dependencies(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    tool = Tool(
        id=uuid.uuid4(),
        user_id=None,
        is_system=True,
        definition_key="builtin:current_datetime",
        name="Current DateTime",
        parameters={},
        enabled=True,
    )
    skill = Skill(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Skill",
        slug="research-skill",
        kind="text",
        storage_path="skills/research/SKILL.md",
        content_hash="d" * 64,
        size_bytes=100,
        origin_kind="created_by_me",
    )
    db.add_all([model, tool, skill])
    item, _version = await _make_agent_item_with_dependencies(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert [row["name"] for row in body["tools"]] == ["Current DateTime"]
    assert [row["name"] for row in body["skills"]] == ["Research Skill"]
    assert [row["name"] for row in body["mcp_tools"]] == ["search_docs"]

    server = (
        await db.execute(
            select(McpServer).where(
                McpServer.user_id == TEST_USER_ID,
                McpServer.name == "Docs MCP",
            )
        )
    ).scalar_one_or_none()
    assert server is not None
    assert server.transport == "streamable_http"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_materializes_unbound_tool_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [
            {
                "name": "Example HTTP Tool",
                "description": "Calls an example API",
                "definition_key": "http_request",
                "parameters": {"url": "https://api.example.test/search"},
            },
            {
                "description": "Fallback name tool",
                "definition_key": "tavily_search",
                "parameters": {"mode": "fallback"},
            }
        ],
        "skills": [],
        "mcp_tools": [],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    agent_id = uuid.UUID(create_response.json()["id"])
    tool = (
        await db.execute(
            select(Tool).where(
                Tool.user_id == TEST_USER_ID,
                Tool.name == "Example HTTP Tool",
            )
        )
    ).scalar_one_or_none()
    assert tool is not None
    assert tool.definition_key == "http_request"
    assert tool.parameters == {"url": "https://api.example.test/search"}
    link = await db.get(AgentToolLink, (agent_id, tool.id))
    assert link is not None
    fallback_tool = (
        await db.execute(
            select(Tool).where(
                Tool.user_id == TEST_USER_ID,
                Tool.name == "tavily_search",
            )
        )
    ).scalar_one_or_none()
    assert fallback_tool is not None
    assert fallback_tool.parameters == {"mode": "fallback"}
    fallback_link = await db.get(AgentToolLink, (agent_id, fallback_tool.id))
    assert fallback_link is not None


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_reuse_existing_requires_credential_bound_tool(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="HTTP Tool Credential",
        data={
            "server_url": "https://api.example.test",
            "access_token": "install-token",
            "refresh_token": "install-refresh",
        },
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    requirement = {
        "key": "tool_http_request",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "HTTP tool credential",
        "description": "Credential for the HTTP tool",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [
            {
                "name": "Example HTTP Tool",
                "description": "Calls an example API",
                "definition_key": "http_request",
                "parameters": {"url": "https://api.example.test/search"},
            }
        ],
        "skills": [],
        "mcp_tools": [],
        "subagents": [],
    }
    payload["setup"] = {
        **payload.get("setup", {}),
        "required_credentials": [requirement],
    }
    version.credential_requirements = [requirement]
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"tool_http_request": str(credential.id)},
        },
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={"dependency_strategy": "reuse_existing"},
    )

    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_does_not_reuse_tool_with_different_parameters(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    existing_tool = Tool(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        is_system=False,
        definition_key="http_request",
        name="Example HTTP Tool",
        description="Wrong local endpoint",
        parameters={"url": "https://wrong.example.test/search"},
        credential_id=None,
        enabled=True,
    )
    db.add_all([model, existing_tool])
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [
            {
                "name": "Example HTTP Tool",
                "description": "Calls an example API",
                "definition_key": "http_request",
                "parameters": {"url": "https://api.example.test/search"},
            }
        ],
        "skills": [],
        "mcp_tools": [],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    agent_id = uuid.UUID(create_response.json()["id"])
    link = (
        await db.execute(select(AgentToolLink).where(AgentToolLink.agent_id == agent_id))
    ).scalar_one()
    tool = await db.get(Tool, link.tool_id)
    assert tool is not None
    assert tool.id != existing_tool.id
    assert tool.parameters == {"url": "https://api.example.test/search"}


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_does_not_reuse_mcp_name_collision(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    existing = McpServer(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Docs MCP",
        description="Different local server",
        transport="streamable_http",
        url="https://wrong.example.test/mcp",
        command=None,
        args=[],
        env_vars={},
        headers={},
        credential_id=None,
        status="connected",
        is_system=False,
    )
    db.add_all([model, existing])
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [],
        "skills": [],
        "mcp_tools": [
            {
                "name": "search_docs",
                "description": "Search docs",
                "input_schema": {"type": "object"},
                "server": {
                    "name": "Docs MCP",
                    "description": "Documentation lookup",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test/mcp",
                    "command": None,
                    "args": [],
                    "env_vars": {},
                    "headers": {},
                },
            }
        ],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    agent_id = uuid.UUID(create_response.json()["id"])
    link = (
        await db.execute(
            select(AgentMcpToolLink).where(AgentMcpToolLink.agent_id == agent_id)
        )
    ).scalar_one()
    tool = await db.get(McpTool, link.mcp_tool_id)
    assert tool is not None
    server = await db.get(McpServer, tool.server_id)
    assert server is not None
    assert server.id != existing.id
    assert server.url == "https://mcp.example.test/mcp"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_does_not_reuse_mcp_with_different_credential(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    old_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Old Docs MCP OAuth",
        data={
            "server_url": "https://mcp.example.test",
            "access_token": "old-token",
            "refresh_token": "old-refresh",
        },
    )
    selected_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Selected Docs MCP OAuth",
        data={
            "server_url": "https://mcp.example.test",
            "access_token": "selected-token",
            "refresh_token": "selected-refresh",
        },
    )
    existing = McpServer(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Docs MCP",
        description="Existing server with a different account",
        transport="streamable_http",
        url="https://mcp.example.test/mcp",
        command=None,
        args=[],
        env_vars={},
        headers={"Authorization": "={{ $credentials.access_token }}"},
        credential_id=old_credential.id,
        status="connected",
        is_system=False,
    )
    tool = Tool(
        id=uuid.uuid4(),
        user_id=None,
        is_system=True,
        definition_key="builtin:current_datetime",
        name="Current DateTime",
        parameters={},
        enabled=True,
    )
    skill = Skill(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Skill",
        slug="research-skill",
        kind="text",
        storage_path="skills/research/SKILL.md",
        content_hash="d" * 64,
        size_bytes=100,
        origin_kind="created_by_me",
    )
    db.add_all([model, existing, tool, skill])
    item, version = await _make_agent_item_with_dependencies(db)
    requirement = {
        "key": "mcp_docs_mcp",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "Docs MCP credential",
        "description": "Credential for the Docs MCP server",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    version.credential_requirements = [requirement]
    version.payload["setup"]["required_credentials"] = [requirement]
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_docs_mcp": str(selected_credential.id)},
        },
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    agent_id = uuid.UUID(create_response.json()["id"])
    link = (
        await db.execute(
            select(AgentMcpToolLink).where(AgentMcpToolLink.agent_id == agent_id)
        )
    ).scalar_one()
    mcp_tool = await db.get(McpTool, link.mcp_tool_id)
    assert mcp_tool is not None
    server = await db.get(McpServer, mcp_tool.server_id)
    assert server is not None
    assert server.id != existing.id
    assert server.credential_id == selected_credential.id


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_reuse_existing_requires_mcp_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [],
        "skills": [],
        "mcp_tools": [
            {
                "name": "search_docs",
                "description": "Search docs",
                "input_schema": {"type": "object"},
                "server": {
                    "name": "Docs MCP",
                    "description": "Documentation lookup",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test/mcp",
                    "command": None,
                    "args": [],
                    "env_vars": {},
                    "headers": {},
                },
            }
        ],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={"dependency_strategy": "reuse_existing"},
    )

    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_agent_blueprint_install_persists_credential_binding_for_create_agent(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Docs MCP OAuth",
        data={
            "server_url": "https://mcp.example.test",
            "access_token": "install-token",
            "refresh_token": "install-refresh",
        },
    )
    tool = Tool(
        id=uuid.uuid4(),
        user_id=None,
        is_system=True,
        definition_key="builtin:current_datetime",
        name="Current DateTime",
        parameters={},
        enabled=True,
    )
    skill = Skill(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Skill",
        slug="research-skill",
        kind="text",
        storage_path="skills/research/SKILL.md",
        content_hash="d" * 64,
        size_bytes=100,
        origin_kind="created_by_me",
    )
    db.add_all([model, tool, skill])
    item, version = await _make_agent_item_with_dependencies(db)
    requirement = {
        "key": "mcp_docs_mcp",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "Docs MCP credential",
        "description": "Credential for the Docs MCP server",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    version.credential_requirements = [requirement]
    version.payload["setup"]["required_credentials"] = [requirement]
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_docs_mcp": str(credential.id)},
        },
    )
    assert install_response.status_code == 201, install_response.text
    assert install_response.json()["install_status"] == "active"
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    server = (
        await db.execute(
            select(McpServer).where(
                McpServer.user_id == TEST_USER_ID,
                McpServer.name == "Docs MCP",
            )
        )
    ).scalar_one()
    assert server.credential_id == credential.id


@pytest.mark.asyncio
async def test_agent_blueprint_reuse_or_update_applies_credential_bindings(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Docs MCP OAuth",
        data={
            "server_url": "https://mcp.example.test",
            "access_token": "install-token",
            "refresh_token": "install-refresh",
        },
    )
    item, version = await _make_agent_item_with_dependencies(db)
    requirement = {
        "key": "mcp_docs_mcp",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "Docs MCP credential",
        "description": "Credential for the Docs MCP server",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    payload = dict(version.payload)
    setup = dict(payload["setup"])
    setup["required_credentials"] = [requirement]
    payload["setup"] = setup
    version.credential_requirements = [requirement]
    version.payload = payload
    await db.commit()

    initial_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert initial_response.status_code == 201, initial_response.text
    assert initial_response.json()["install_status"] == "needs_setup"
    installation_id = initial_response.json()["id"]
    blueprint_id = uuid.UUID(initial_response.json()["installed_agent_blueprint_id"])

    retry_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "reuse_or_update",
            "credential_bindings": {"mcp_docs_mcp": str(credential.id)},
        },
    )

    assert retry_response.status_code == 201, retry_response.text
    assert retry_response.json()["id"] == installation_id
    assert retry_response.json()["install_status"] == "active"
    blueprint = await db.get(AgentBlueprint, blueprint_id)
    assert blueprint is not None
    assert blueprint.install_status == "active"
    assert blueprint.credential_bindings == {"mcp_docs_mcp": str(credential.id)}


@pytest.mark.asyncio
async def test_agent_blueprint_installation_summary_recomputes_missing_binding(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Docs MCP OAuth",
        data={
            "server_url": "https://mcp.example.test",
            "access_token": "install-token",
            "refresh_token": "install-refresh",
        },
    )
    item, version = await _make_agent_item_with_dependencies(db)
    requirement = {
        "key": "mcp_docs_mcp",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "Docs MCP credential",
        "description": "Credential for the Docs MCP server",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    payload = dict(version.payload)
    setup = dict(payload["setup"])
    setup["required_credentials"] = [requirement]
    payload["setup"] = setup
    version.credential_requirements = [requirement]
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_docs_mcp": str(credential.id)},
        },
    )
    assert install_response.status_code == 201, install_response.text
    assert install_response.json()["install_status"] == "active"
    blueprint = await db.get(
        AgentBlueprint,
        uuid.UUID(install_response.json()["installed_agent_blueprint_id"]),
    )
    assert blueprint is not None
    blueprint.credential_bindings = {}
    await db.commit()

    detail_response = await client.get(f"/api/marketplace/items/{item.id}")

    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["installation"]["status"] == "needs_setup"
    list_response = await client.get("/api/marketplace/items?resource_type=agent")
    assert list_response.status_code == 200, list_response.text
    listed = next(row for row in list_response.json() if row["id"] == str(item.id))
    assert listed["installation"]["status"] == "needs_setup"


@pytest.mark.asyncio
async def test_agent_blueprint_install_rejects_wrong_credential_definition(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    wrong_credential = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai",
        name="Wrong Credential",
        data={"api_key": "sk-test"},
    )
    item, version = await _make_agent_item_with_dependencies(db)
    requirement = {
        "key": "mcp_docs_mcp",
        "definition_key": "mcp_oauth2",
        "required": True,
        "label": "Docs MCP credential",
        "description": "Credential for the Docs MCP server",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    version.credential_requirements = [requirement]
    version.payload["setup"]["required_credentials"] = [requirement]
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "new_copy",
            "credential_bindings": {"mcp_docs_mcp": str(wrong_credential.id)},
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_REQUIRED"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_rejects_missing_skill_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [],
        "skills": [
            {
                "name": "Missing Research Skill",
                "slug": "missing-research-skill",
                "kind": "text",
            }
        ],
        "mcp_tools": [],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_links_existing_subagent(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    worker_id = uuid.uuid4()
    worker = Agent(
        id=worker_id,
        user_id=TEST_USER_ID,
        name="Worker Agent",
        description="Handles delegated work",
        system_prompt="Handle delegated work.",
        runtime_name=make_agent_runtime_name(worker_id),
        identity_mode="per_user",
        model_id=model.id,
        status="active",
    )
    db.add_all([model, worker])
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [],
        "skills": [],
        "mcp_tools": [],
        "subagents": [
            {
                "name": "Worker Agent",
                "description": "Handles delegated work",
                "position": 0,
            }
        ],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert [row["id"] for row in body["sub_agents"]] == [str(worker_id)]
    assert [row["name"] for row in body["sub_agents"]] == ["Worker Agent"]


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_rejects_missing_subagent_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [],
        "skills": [],
        "mcp_tools": [],
        "subagents": [
            {
                "name": "Missing Worker Agent",
                "description": "Handles delegated work",
                "position": 0,
            }
        ],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_update_agent_blueprint_installation_overwrites_blueprint(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["id"]
    blueprint_id = uuid.UUID(install_response.json()["installed_agent_blueprint_id"])

    new_payload = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "agent": {
            "name": "Updated Research Agent",
            "description": "Updated description",
            "system_prompt": "You research updated topics.",
            "model": {"provider": "openai", "model_name": "gpt-5-mini"},
        },
        "capabilities": {"tools": [], "skills": [], "mcp_tools": [], "subagents": []},
        "setup": {"required_credentials": [], "warnings": [], "blocked_dependencies": []},
    }
    latest = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="agent-2",
        version_number=2,
        resource_type="agent",
        payload_kind="agent_spec",
        payload=new_payload,
        storage_path=None,
        content_hash="e" * 64,
        size_bytes=1024,
        credential_requirements=[],
        dependency_requirements=[],
        execution_profile={},
        created_by=TEST_USER_ID,
    )
    db.add(latest)
    await db.flush()
    item.latest_version_id = latest.id
    await db.commit()

    update_response = await client.post(
        f"/api/marketplace/installations/{installation_id}/update",
        json={"strategy": "overwrite"},
    )

    assert update_response.status_code == 200, update_response.text
    blueprint = await db.get(AgentBlueprint, blueprint_id)
    assert blueprint is not None
    assert blueprint.name == "Updated Research Agent"
    assert blueprint.spec == new_payload
    assert blueprint.source_marketplace_version_id == latest.id


@pytest.mark.asyncio
async def test_update_agent_blueprint_installation_normalizes_version_requirements(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["id"]
    blueprint_id = uuid.UUID(install_response.json()["installed_agent_blueprint_id"])
    requirement = {
        "key": "llm",
        "definition_key": "openai",
        "required": True,
        "label": "OpenAI credential",
        "description": "Credential used by the blueprint model",
        "fields": [],
        "injection": "config",
        "scope": "user",
    }
    new_payload = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "agent": {
            "name": "Updated Research Agent",
            "description": "Updated description",
            "system_prompt": "You research updated topics.",
            "model": {"provider": "openai", "model_name": "gpt-5-mini"},
        },
        "capabilities": {"tools": [], "skills": [], "mcp_tools": [], "subagents": []},
        "setup": {"warnings": [], "blocked_dependencies": []},
    }
    latest = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="agent-2",
        version_number=2,
        resource_type="agent",
        payload_kind="agent_spec",
        payload=new_payload,
        storage_path=None,
        content_hash="f" * 64,
        size_bytes=1024,
        credential_requirements=[requirement],
        dependency_requirements=[],
        execution_profile={},
        created_by=TEST_USER_ID,
    )
    db.add(latest)
    await db.flush()
    item.latest_version_id = latest.id
    await db.commit()

    update_response = await client.post(
        f"/api/marketplace/installations/{installation_id}/update",
        json={"strategy": "overwrite"},
    )

    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["install_status"] == "needs_setup"
    blueprint = await db.get(AgentBlueprint, blueprint_id)
    assert blueprint is not None
    assert blueprint.spec["setup"]["required_credentials"] == [requirement]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )
    assert create_response.status_code == 409, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_REQUIRED"


@pytest.mark.asyncio
async def test_delete_agent_blueprint_installation_with_resource_removes_blueprint(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, _version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = uuid.UUID(install_response.json()["id"])
    blueprint_id = uuid.UUID(install_response.json()["installed_agent_blueprint_id"])

    delete_response = await client.delete(
        f"/api/marketplace/installations/{installation_id}",
        params={"delete_resource": True},
    )

    assert delete_response.status_code == 204
    assert await db.get(AgentBlueprint, blueprint_id) is None
    assert await db.get(MarketplaceInstallation, installation_id) is None

@pytest.mark.asyncio
async def test_agent_blueprint_list_ignores_uninstalled_installation_rows(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A blueprint joined against a soft-deleted installation row must not
    duplicate in the list nor surface the stale ``uninstalled`` state."""

    await _ensure_test_user(db)
    item, version = await _make_agent_item(db)
    blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Agent",
        spec=version.payload,
        spec_hash="b" * 64,
        source_marketplace_item_id=item.id,
        source_marketplace_version_id=version.id,
        origin_user_id=TEST_USER_ID,
        origin_kind="imported_by_me",
        install_status="active",
    )
    db.add(blueprint)
    await db.flush()
    stale = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=blueprint.id,
        install_status="uninstalled",
        is_dirty=False,
        installed_at=_now(),
    )
    active = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=blueprint.id,
        install_status="active",
        is_dirty=False,
        installed_at=_now(),
    )
    db.add_all([stale, active])
    await db.commit()

    list_response = await client.get("/api/agent-blueprints")

    assert list_response.status_code == 200, list_response.text
    rows = [row for row in list_response.json() if row["id"] == str(blueprint.id)]
    assert len(rows) == 1
    assert rows[0]["install_status"] == "active"
    assert rows[0]["installation_id"] == str(active.id)

    detail_response = await client.get(f"/api/agent-blueprints/{blueprint.id}")
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["installation_id"] == str(active.id)
    assert detail_response.json()["install_status"] == "active"


@pytest.mark.asyncio
async def test_agent_blueprint_list_after_uninstall_and_reinstall(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, _version = await _make_agent_item(db)
    await db.commit()

    first_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert first_response.status_code == 201, first_response.text
    first_installation_id = first_response.json()["id"]
    first_blueprint_id = first_response.json()["installed_agent_blueprint_id"]

    delete_response = await client.delete(
        f"/api/marketplace/installations/{first_installation_id}"
    )
    assert delete_response.status_code == 204, delete_response.text

    # The soft-uninstalled blueprint must vanish from list + detail.
    after_uninstall = await client.get("/api/agent-blueprints")
    assert after_uninstall.status_code == 200, after_uninstall.text
    assert all(
        row["id"] != first_blueprint_id for row in after_uninstall.json()
    )
    detail_404 = await client.get(f"/api/agent-blueprints/{first_blueprint_id}")
    assert detail_404.status_code == 404, detail_404.text
    assert detail_404.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"

    second_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert second_response.status_code == 201, second_response.text
    second_blueprint_id = second_response.json()["installed_agent_blueprint_id"]

    list_response = await client.get("/api/agent-blueprints")

    assert list_response.status_code == 200, list_response.text
    rows = list_response.json()
    ids = [row["id"] for row in rows]
    # Exactly one active blueprint, zero ghosts (the soft-uninstalled
    # first copy is hidden).
    assert ids.count(second_blueprint_id) == 1
    assert first_blueprint_id not in ids
    assert all(row["install_status"] != "uninstalled" for row in rows)
    active_row = next(row for row in rows if row["id"] == second_blueprint_id)
    assert active_row["install_status"] == "active"


@pytest.mark.asyncio
async def test_agent_blueprint_list_omits_spec_detail_includes_it(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, version = await _make_agent_item(db)
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    list_response = await client.get("/api/agent-blueprints")
    assert list_response.status_code == 200, list_response.text
    listed = next(row for row in list_response.json() if row["id"] == blueprint_id)
    assert listed["spec"] is None

    detail_response = await client.get(f"/api/agent-blueprints/{blueprint_id}")
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["spec"] == version.payload


@pytest.mark.asyncio
async def test_agent_blueprint_reuse_or_update_rejects_foreign_blueprint(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """reuse_or_update must re-validate blueprint ownership before mutating
    bindings (collapsed to 404 per the enumeration-safety convention)."""

    await _ensure_test_user(db)
    other_user = User(
        id=uuid.uuid4(),
        email="other-blueprint@test.com",
        name="Other User",
        hashed_password="h",
        is_active=True,
        is_super_user=False,
    )
    db.add(other_user)
    item, version = await _make_agent_item(db)
    foreign_blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=other_user.id,
        name="Foreign Blueprint",
        spec=version.payload,
        spec_hash="b" * 64,
        origin_user_id=other_user.id,
        origin_kind="imported_by_me",
        install_status="active",
    )
    db.add(foreign_blueprint)
    await db.flush()
    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=foreign_blueprint.id,
        install_status="active",
        is_dirty=False,
        installed_at=_now(),
    )
    db.add(installation)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={
            "install_mode": "reuse_or_update",
            "credential_bindings": {"mcp_docs_mcp": str(uuid.uuid4())},
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_rejects_unknown_tool_definition_key(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [
            {
                "name": "Phantom Tool",
                "definition_key": "registry:not_a_real_tool",
                "parameters": {},
            }
        ],
        "skills": [],
        "mcp_tools": [],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 422, create_response.text
    body = create_response.json()
    assert body["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"
    assert "registry:not_a_real_tool" in body["error"]["message"]
    created = (
        await db.execute(select(Tool).where(Tool.user_id == TEST_USER_ID))
    ).scalars().all()
    assert created == []


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_rejects_builtin_tool_without_system_row(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """``builtin:*`` dependencies must never materialize user-owned Tool
    copies — when no system tool matches, the create is rejected."""

    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    payload["capabilities"] = {
        "tools": [
            {
                "name": "Current DateTime",
                "definition_key": "builtin:current_datetime",
                "parameters": {},
            }
        ],
        "skills": [],
        "mcp_tools": [],
        "subagents": [],
    }
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"
    copies = (
        await db.execute(
            select(Tool).where(
                Tool.user_id == TEST_USER_ID,
                Tool.definition_key == "builtin:current_datetime",
            )
        )
    ).scalars().all()
    assert copies == []


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_rejects_unknown_middleware_key(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    agent_spec = dict(payload["agent"])
    agent_spec["middleware_configs"] = [
        {"type": "totally_unknown_middleware", "params": {}}
    ]
    payload["agent"] = agent_spec
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 422, create_response.text
    body = create_response.json()
    assert body["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"
    assert "totally_unknown_middleware" in body["error"]["message"]


@pytest.mark.asyncio
async def test_create_agent_from_blueprint_accepts_known_middleware_config(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        is_default=True,
        is_visible=True,
    )
    db.add(model)
    item, version = await _make_agent_item(db)
    payload = dict(version.payload)
    agent_spec = dict(payload["agent"])
    agent_spec["middleware_configs"] = [{"type": "summarization", "params": {}}]
    payload["agent"] = agent_spec
    version.payload = payload
    await db.commit()

    install_response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "new_copy"},
    )
    assert install_response.status_code == 201, install_response.text
    blueprint_id = install_response.json()["installed_agent_blueprint_id"]

    create_response = await client.post(
        f"/api/agent-blueprints/{blueprint_id}/create-agent",
        json={},
    )

    assert create_response.status_code == 201, create_response.text
    agent = await db.get(Agent, uuid.UUID(create_response.json()["id"]))
    assert agent is not None
    assert [row["type"] for row in agent.middleware_configs] == ["summarization"]
