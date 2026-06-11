"""Marketplace Agent publish tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import AGENT_IDENTITY_FIXED
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.models.marketplace import (
    MarketplaceItem,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import AgentSkillLink, Skill
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


async def _make_agent_with_dependencies(
    db: AsyncSession,
    *,
    model_params: dict | None = None,
    include_skill: bool = True,
    fallback_model: Model | None = None,
    mcp_headers: dict | None = None,
) -> tuple[Agent, Model]:
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
        description="Returns the current date and time.",
        parameters={"timezone": "Asia/Seoul"},
        enabled=True,
    )
    skill = Skill(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Skill",
        slug=f"research-skill-{uuid.uuid4().hex[:8]}",
        description="Research helper",
        kind="text",
        storage_path="skills/example/SKILL.md",
        content_hash="a" * 64,
        size_bytes=100,
        version="1.0.0",
        origin_kind="created_by_me",
    )
    server = McpServer(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Docs MCP",
        description="Documentation lookup",
        transport="streamable_http",
        url="https://mcp.example.test/mcp",
        headers=mcp_headers or {},
        env_vars={},
        status="connected",
    )
    mcp_tool = McpTool(
        id=uuid.uuid4(),
        server_id=server.id,
        name="search_docs",
        description="Search docs",
        input_schema={"type": "object"},
        enabled=True,
        last_seen_at=_now(),
    )
    agent_id = uuid.uuid4()
    agent = Agent(
        id=agent_id,
        user_id=TEST_USER_ID,
        name="Research Agent",
        description="Researches topics",
        system_prompt="You research topics and cite sources.",
        runtime_name=f"agent_{agent_id.hex[:16]}",
        identity_mode=AGENT_IDENTITY_FIXED,
        model_id=model.id,
        model_params=model_params or {"temperature": 0.2},
        middleware_configs=[{"type": "todo", "enabled": True}],
        opener_questions=["What should I research?"],
        model_fallback_list=[str(fallback_model.id)] if fallback_model else None,
        status="active",
    )
    agent.tool_links = [AgentToolLink(tool_id=tool.id)]
    if include_skill:
        agent.skill_links = [AgentSkillLink(skill_id=skill.id)]
    agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mcp_tool.id)]

    rows: list[object] = [model, tool, server, mcp_tool, agent]
    if include_skill:
        rows.append(skill)
    db.add_all(rows)
    await db.flush()
    return agent, model


@pytest.mark.asyncio
async def test_publish_agent_creates_portable_agent_spec(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "public",
            "name": "Research Agent Blueprint",
            "description": "Shareable research agent",
            "tags": ["research"],
            "categories": ["productivity"],
            "release_notes": "Initial share",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "agent"
    assert body["latest_version"]["id"]

    version = await db.get(MarketplaceVersion, uuid.UUID(body["latest_version"]["id"]))
    assert version is not None
    assert version.resource_type == "agent"
    assert version.payload_kind == "agent_spec"
    assert version.storage_path is None
    assert version.size_bytes > 0

    payload = version.payload
    assert payload["resource"] == "agent_blueprint"
    assert payload["agent"]["name"] == "Research Agent"
    assert payload["agent"]["system_prompt"] == "You research topics and cite sources."
    assert payload["agent"]["model"] == {
        "provider": "openai",
        "model_name": "gpt-5-mini",
        "display_name": "GPT-5 Mini",
        "base_url": None,
    }
    assert payload["capabilities"]["tools"][0]["definition_key"] == "builtin:current_datetime"
    assert payload["capabilities"]["skills"] == []
    assert payload["capabilities"]["mcp_tools"][0]["name"] == "search_docs"
    assert payload["capabilities"]["mcp_tools"][0]["server"]["transport"] == "streamable_http"

    raw_payload = json.dumps(payload, sort_keys=True)
    assert str(agent.id) not in raw_payload
    assert str(model.id) not in raw_payload
    assert "credential_id" not in raw_payload

    link = (
        await db.execute(
            select(MarketplacePublicationLink).where(
                MarketplacePublicationLink.item_id == uuid.UUID(body["id"])
            )
        )
    ).scalar_one()
    assert link.resource_type == "agent"
    assert link.source_agent_id == agent.id

    # Audit row is committed together with the publish.
    audit_row = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.action == "marketplace.publish")
        )
    ).scalar_one()
    assert audit_row.target_id == body["id"]


@pytest.mark.asyncio
async def test_publish_agent_audit_failure_rolls_back_publish(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Publish and its audit row share a single transaction — when the
    audit write fails, the publish must roll back instead of committing
    without an audit trail."""

    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    from app.services import audit_service

    with (
        patch.object(
            audit_service, "record_event", side_effect=RuntimeError("audit down")
        ),
        pytest.raises(RuntimeError),
    ):
        await client.post(
            f"/api/marketplace/items/from-agent/{agent.id}",
            json={"visibility": "public", "name": "Atomic Agent Blueprint"},
        )

    items = (await db.execute(select(MarketplaceItem))).scalars().all()
    assert items == []
    audit_rows = (await db.execute(select(AuditEvent))).scalars().all()
    assert audit_rows == []


@pytest.mark.asyncio
async def test_publish_agent_rejects_non_portable_skill_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=True)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "public",
            "name": "Non Portable Agent",
            "description": "Uses a local skill",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_publish_agent_rejects_unbound_mcp_credential_template(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(
        db,
        include_skill=False,
        mcp_headers={"Authorization": "={{ $credentials.access_token }}"},
    )
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "public",
            "name": "Unbound MCP Agent Blueprint",
            "description": "Uses a credential template without a credential binding",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_publish_agent_rejects_public_stdio_mcp_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    server = (
        await db.execute(
            select(McpServer).where(
                McpServer.user_id == TEST_USER_ID,
                McpServer.name == "Docs MCP",
            )
        )
    ).scalar_one()
    server.transport = "stdio"
    server.url = None
    server.command = "npx"
    server.args = ["dangerous-mcp"]
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "public",
            "name": "Stdio Agent Blueprint",
            "description": "Uses a local stdio MCP server",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_patch_agent_item_rejects_public_stdio_mcp_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    server = (
        await db.execute(
            select(McpServer).where(
                McpServer.user_id == TEST_USER_ID,
                McpServer.name == "Docs MCP",
            )
        )
    ).scalar_one()
    server.transport = "stdio"
    server.url = None
    server.command = "npx"
    server.args = ["dangerous-mcp"]
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "private",
            "name": "Private Stdio Agent Blueprint",
        },
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
async def test_patch_agent_item_rejects_shared_skill_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=True)
    await db.commit()

    publish_response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "private",
            "name": "Private Skill Agent Blueprint",
        },
    )
    assert publish_response.status_code == 201, publish_response.text
    item_id = publish_response.json()["id"]

    response = await client.patch(
        f"/api/marketplace/items/{item_id}",
        json={"visibility": "unlisted"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_publish_agent_rejects_shared_subagent_dependency(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, model = await _make_agent_with_dependencies(db, include_skill=False)
    child_id = uuid.uuid4()
    child = Agent(
        id=child_id,
        user_id=TEST_USER_ID,
        name="Research Subagent",
        description="Handles delegated work",
        system_prompt="Help the parent agent.",
        runtime_name=f"agent_{child_id.hex[:16]}",
        identity_mode=AGENT_IDENTITY_FIXED,
        model_id=model.id,
        status="active",
    )
    db.add(child)
    await db.flush()
    db.add(
        AgentSubAgentLink(
            parent_agent_id=agent.id,
            sub_agent_id=child.id,
            position=0,
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "unlisted",
            "name": "Delegating Agent Blueprint",
            "description": "Requires a subagent by name",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_publish_agent_preserves_fallback_model_descriptors(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    fallback_model = Model(
        id=uuid.uuid4(),
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        is_default=False,
        is_visible=True,
    )
    db.add(fallback_model)
    agent, _model = await _make_agent_with_dependencies(
        db,
        include_skill=False,
        fallback_model=fallback_model,
    )
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "public",
            "name": "Fallback Agent Blueprint",
        },
    )

    assert response.status_code == 201, response.text
    version = await db.get(
        MarketplaceVersion,
        uuid.UUID(response.json()["latest_version"]["id"]),
    )
    assert version is not None
    assert version.payload["agent"]["model_fallbacks"] == [
        {
            "provider": "anthropic",
            "model_name": "claude-sonnet-4-5",
            "display_name": "Claude Sonnet 4.5",
            "base_url": None,
        }
    ]


@pytest.mark.asyncio
async def test_publish_agent_rejects_secret_like_payload(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(
        db,
        model_params={"api_key": "not-for-marketplace"},
    )
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "private",
            "name": "Unsafe Agent",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_publish_agent_version_rejects_non_agent_marketplace_item(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db)
    skill_item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=TEST_USER_ID,
        is_system=False,
        is_listed=True,
        name="Existing Skill Item",
        slug=f"existing-skill-{uuid.uuid4().hex[:8]}",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
        published_at=_now(),
    )
    db.add(skill_item)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/{skill_item.id}/versions/from-agent/{agent.id}",
        json={"release_notes": "Should not attach agent versions to skills"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_INVALID_PACKAGE"


@pytest.mark.asyncio
async def test_publish_agent_rejects_overlong_name(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """``name`` is capped at 120 chars so an oversized title can't be
    persisted into the catalog (schema ground truth)."""

    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={"visibility": "private", "name": "x" * 121},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_publish_agent_rejects_empty_name(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={"visibility": "private", "name": ""},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_publish_agent_rejects_overlong_release_notes(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "private",
            "name": "Verbose Agent",
            "release_notes": "r" * 4001,
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_publish_agent_rejects_overlong_description(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    agent, _model = await _make_agent_with_dependencies(db, include_skill=False)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-agent/{agent.id}",
        json={
            "visibility": "private",
            "name": "Wordy Agent",
            "description": "d" * 2001,
        },
    )

    assert response.status_code == 422, response.text
