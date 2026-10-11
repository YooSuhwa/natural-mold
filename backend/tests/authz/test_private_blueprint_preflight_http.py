"""Actual JWT/CSRF Blueprint route rejects every late payload failure before DML."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import Tool
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("skill", 422, "MARKETPLACE_INVALID_PACKAGE"),
        ("fallback", 400, "HTTP_400"),
        ("middleware", 422, "MARKETPLACE_INVALID_PACKAGE"),
        ("policy", 422, "MARKETPLACE_INVALID_PACKAGE"),
        ("identity", 422, "AGENT_IDENTITY_INVALID"),
    ],
)
async def test_http_denied_when_late_payload_invalid(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
    failure: Literal["skill", "fallback", "middleware", "policy", "identity"],
    status: int,
    code: str,
) -> None:
    # Given: real authenticated actor and first Tool requiring creation.
    root = blueprint(private_scopes.first, {"tools": new_tool()})
    body: dict[str, JsonValue] = {
        "model_id": str(agent_case.model_id),
        "dependency_strategy": "install_missing",
    }
    match failure:
        case "skill":
            foreign = private_scopes.second
            db.add(
                Skill(
                    user_id=foreign.user_id,
                    org_id=foreign.org_id,
                    tenant_id=foreign.tenant_id,
                    name="Foreign",
                    slug="foreign",
                    kind="text",
                )
            )
            root.spec["capabilities"]["skills"] = [{"slug": "foreign"}]
        case "fallback":
            body["model_fallback_ids"] = [str(uuid4())]
        case "middleware":
            root.spec["agent"]["middleware_configs"] = [{"type": "nonexistent"}]
        case "policy":
            root.spec["agent"]["runtime_policy"] = {"version": 99}
        case "identity":
            root.spec["agent"]["identity_mode"] = "invalid"
        case unreachable:
            assert_never(unreachable)
    db.add(root)
    await db.commit()
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    # When: actual JWT/active-org/CSRF HTTP action handles invalid late input.
    with materialization_sql(db) as dml:
        response = await agent_case.client.post(
            f"/api/agent-blueprints/{root.id}/create-agent",
            json=body,
            headers={"X-CSRF-Token": csrf},
        )
    # Then: the established response is preserved and no materialization SQL ran.
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    assert dml == []


async def test_http_control_when_complete_payload_is_accepted(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
) -> None:
    # Given: real authentication with scoped graph, credentials and portable settings.
    own = private_scopes.first
    skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Own",
        slug="own",
        kind="text",
    )
    fallback = Model(provider="openai", model_name="fallback", display_name="Visible fallback")
    hidden = Model(provider="openai", model_name="hidden", display_name="Hidden", is_visible=False)
    llm = Credential(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="LLM",
        definition_key="openai_api",
        data_encrypted="unused",
        key_id="fixture",
        field_keys=[],
        status="active",
    )
    mcp = Credential(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="MCP",
        definition_key="mcp_secret",
        data_encrypted="unused",
        key_id="fixture",
        field_keys=[],
        status="active",
    )
    root = blueprint(
        own,
        {
            "tools": new_tool(),
            "skills": [{"slug": "own"}],
            "mcp_tools": [{"name": "search", "server": {"name": "Remote"}}],
        },
    )
    root.spec["agent"].update(
        {
            "middleware_configs": [{"type": "summarization", "params": {}}],
            "runtime_policy": {"version": 1, "todo": {"enabled": False}},
            "identity_mode": "fixed",
        }
    )
    root.spec["setup"] = {
        "required_credentials": [
            {"key": "llm", "required": True, "definition_key": "openai_api"},
            {"key": "mcp_remote", "required": True, "definition_key": "mcp_secret"},
        ]
    }
    db.add_all([root, skill, fallback, hidden, llm, mcp])
    await db.commit()
    fallback_order = [str(fallback.id), str(hidden.id), str(fallback.id)]
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    # When: full real route accepts graph and all final Agent settings.
    with materialization_sql(db) as dml:
        response = await agent_case.client.post(
            f"/api/agent-blueprints/{root.id}/create-agent",
            json={
                "model_id": str(agent_case.model_id),
                "model_fallback_ids": fallback_order,
                "credential_bindings": {"llm": str(llm.id), "mcp_remote": str(mcp.id)},
            },
            headers={"X-CSRF-Token": csrf},
        )
    # Then: exact accepted bindings, scope and payload survive persistence.
    assert response.status_code == 201, response.text
    agent = await db.get(Agent, UUID(response.json()["id"]))
    assert agent is not None
    tool = await db.scalar(select(Tool).where(Tool.name == "New planned tool"))
    server = await db.scalar(select(McpServer).where(McpServer.name == "Remote"))
    assert tool is not None and server is not None
    mcp_tool = await db.scalar(select(McpTool).where(McpTool.server_id == server.id))
    assert mcp_tool is not None
    expected_scope = (own.user_id, own.org_id, own.tenant_id)
    assert all(
        (row.user_id, row.org_id, row.tenant_id) == expected_scope for row in [agent, tool, server]
    )
    assert agent.llm_credential_id == llm.id and server.credential_id == mcp.id
    assert agent.model_fallback_list == fallback_order
    assert agent.middleware_configs == [{"type": "summarization", "params": {}}]
    assert agent.runtime_policy is not None and agent.runtime_policy["todo"] == {"enabled": False}
    assert agent.identity_mode == "fixed"
    await db.refresh(agent, ["tool_links", "skill_links", "mcp_tool_links"])
    assert [link.tool_id for link in agent.tool_links] == [tool.id]
    assert [link.skill_id for link in agent.skill_links] == [skill.id]
    assert [link.mcp_tool_id for link in agent.mcp_tool_links] == [mcp_tool.id]
    await db.refresh(root, ["created_agent_count"])
    assert root.created_agent_count == 1
    assert dml and any(statement.startswith("UPDATE agent_blueprints") for statement in dml)
