"""Private linked targets remain scoped in actual HTTP presentation and publication."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentUpdate
from app.services import agent_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]
SOURCE_PATH = Path(agent_service.__file__).resolve()
EXPECTED_ROOT = Path(__file__).resolve().parents[2]
Kind = Literal["skill", "tool", "mcp", "subagent"]


@pytest.mark.parametrize("kind", ["skill", "tool", "mcp", "subagent"])
@pytest.mark.parametrize("surface", ["detail", "list", "publish"])
@pytest.mark.parametrize("moved", [False, True])
async def test_http_agent_projection_never_exposes_foreign_private_children(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
    kind: Kind,
    surface: Literal["detail", "list", "publish"],
    moved: bool,
) -> None:
    # Given: production Agent creation, active same-user memberships and valid A child.
    assert SOURCE_PATH.is_relative_to(EXPECTED_ROOT)
    own, foreign = private_scopes.first, private_scopes.second
    marker = "F4_DUMMY_PRIVATE_CHILD_" + kind
    match kind:
        case "skill":
            child = Skill(
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name=marker,
                slug="f4-child",
                kind="text",
                description=marker,
            )
            db.add(child)
            await db.flush()
            db.add(AgentSkillLink(agent_id=agent_case.agent_id, skill_id=child.id))
        case "tool":
            child = Tool(
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name=marker,
                definition_key="http_request",
            )
            db.add(child)
            await db.flush()
            db.add(AgentToolLink(agent_id=agent_case.agent_id, tool_id=child.id))
        case "mcp":
            child = McpServer(
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name=marker,
                transport="streamable_http",
                url="https://invalid.local",
            )
            db.add(child)
            await db.flush()
            mcp_tool = McpTool(server_id=child.id, name=marker, enabled=True)
            db.add(mcp_tool)
            await db.flush()
            db.add(AgentMcpToolLink(agent_id=agent_case.agent_id, mcp_tool_id=mcp_tool.id))
        case "subagent":
            child = Agent(
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name=marker,
                system_prompt="Dummy",
                model_id=agent_case.model_id,
                description=marker,
            )
            db.add(child)
            await db.flush()
            db.add(
                AgentSubAgentLink(
                    parent_agent_id=agent_case.agent_id, sub_agent_id=child.id, position=0
                )
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    endpoint = f"/api/agents/{agent_case.agent_id}" if surface == "detail" else "/api/agents"
    normal = await agent_case.client.get(endpoint)
    assert normal.status_code == 200 and marker in normal.text
    if moved:
        await db.execute(
            update(type(child))
            .where(type(child).id == child.id)
            .values(org_id=foreign.org_id, tenant_id=foreign.tenant_id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        persisted = (
            await db.execute(
                select(type(child).org_id, type(child).tenant_id).where(type(child).id == child.id)
            )
        ).one()
        assert persisted == (foreign.org_id, foreign.tenant_id)
    # When: the actual HTTP projection executes with selected A and its still-owned Agent.
    if surface == "publish":
        csrf = agent_case.client.cookies.get("moldy_csrf")
        assert csrf is not None
        response = await agent_case.client.post(
            f"/api/marketplace/items/from-agent/{agent_case.agent_id}",
            json={"name": "Scoped publication", "visibility": "private"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == (404 if moved else 201), response.text
        return
    response = await agent_case.client.get(endpoint)
    # Then: normal graph works; surviving foreign children must be denied or omitted.
    if moved:
        assert marker not in response.text
    else:
        assert response.status_code == 200 and marker in response.text


async def test_read_projection_preserves_hidden_links_and_writer_can_replace_them(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    skill = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign retained linkage",
        slug="foreign-retained",
        kind="text",
    )
    db.add(skill)
    await db.flush()
    db.add(AgentSkillLink(agent_id=agent_case.agent_id, skill_id=skill.id))
    await db.commit()
    response = await agent_case.client.get(f"/api/agents/{agent_case.agent_id}")
    assert response.status_code == 200
    assert response.json()["skills"] == []
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AgentSkillLink)
            .where(AgentSkillLink.agent_id == agent_case.agent_id)
        )
        == 1
    )
    parent = await agent_service.get_agent(db, agent_case.agent_id, own)
    assert parent is not None
    await agent_service.update_agent(db, parent, AgentUpdate(skill_ids=[]), context=own)
    await db.commit()
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AgentSkillLink)
            .where(AgentSkillLink.agent_id == agent_case.agent_id)
        )
        == 0
    )


async def test_favorite_response_omits_foreign_child_while_preserving_link(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
) -> None:
    foreign = private_scopes.second
    skill = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign favorite linkage",
        slug="foreign-favorite",
        kind="text",
    )
    db.add(skill)
    await db.flush()
    db.add(AgentSkillLink(agent_id=agent_case.agent_id, skill_id=skill.id))
    await db.commit()
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    response = await agent_case.client.patch(
        f"/api/agents/{agent_case.agent_id}/favorite", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["is_favorite"] is True
    assert response.json()["skills"] == []
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AgentSkillLink)
            .where(AgentSkillLink.agent_id == agent_case.agent_id)
        )
        == 1
    )


@pytest.mark.parametrize("kind", ["tool", "mcp"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_summary_counts_follow_persisted_child_scope(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
    kind: Literal["tool", "mcp"],
    foreign: bool,
) -> None:
    context = private_scopes.second if foreign else private_scopes.first
    if kind == "tool":
        child = Tool(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name="Summary child",
            definition_key="http_request",
        )
        db.add(child)
        await db.flush()
        db.add(AgentToolLink(agent_id=agent_case.agent_id, tool_id=child.id))
    else:
        server = McpServer(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name="Summary server",
            transport="streamable_http",
            url="https://invalid.local",
        )
        db.add(server)
        await db.flush()
        child = McpTool(server_id=server.id, name="summary_child", enabled=True)
        db.add(child)
        await db.flush()
        db.add(AgentMcpToolLink(agent_id=agent_case.agent_id, mcp_tool_id=child.id))
    await db.commit()
    response = await agent_case.client.get("/api/agents/summary")
    assert response.status_code == 200
    assert response.json()[0]["tool_count"] == (0 if foreign else 1)
