"""A rejected complete dependency graph never stages or executes graph mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import Tool
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("kind", ["skill", "subagent", "builtin"])
@pytest.mark.parametrize("denied", [False, True], ids=["control", "denied"])
async def test_tool_graph_preflight_when_late_dependency_resolves(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    kind: Literal["skill", "subagent", "builtin"],
    denied: bool,
) -> None:
    # Given: Tool creation precedes an own or foreign dependency.
    own = private_scopes.first
    target = private_scopes.second if denied else own
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    capabilities: dict[str, JsonValue] = {"tools": new_tool()}
    match kind:
        case "skill":
            db.add(
                Skill(
                    user_id=target.user_id,
                    org_id=target.org_id,
                    tenant_id=target.tenant_id,
                    name="Late Skill",
                    slug="late",
                    kind="text",
                )
            )
            capabilities["skills"] = [{"slug": "late", "name": "Late Skill", "kind": "text"}]
        case "subagent":
            db.add(
                Agent(
                    user_id=target.user_id,
                    org_id=target.org_id,
                    tenant_id=target.tenant_id,
                    name="Late SubAgent",
                    system_prompt="Fixture",
                    model_id=model.id,
                )
            )
            capabilities["subagents"] = [{"name": "Late SubAgent"}]
        case "builtin":
            if not denied:
                db.add(
                    Tool(
                        id=uuid4(),
                        user_id=None,
                        is_system=True,
                        name="Clock",
                        definition_key="builtin:current_datetime",
                        enabled=True,
                    )
                )
            capabilities["tools"] = [*new_tool(), {"definition_key": "builtin:current_datetime"}]
        case unreachable:
            assert_never(unreachable)
    root = blueprint(own, capabilities)
    db.add(root)
    await db.commit()
    # When: the actual service decides the entire graph.
    with materialization_sql(db) as dml:
        if denied:
            with pytest.raises(AppError) as caught:
                await create_agent_from_blueprint(
                    db,
                    blueprint_id=root.id,
                    context=own,
                    body=CreateAgentFromBlueprintIn(model_id=model.id),
                )
            assert caught.value.status == 422
        else:
            agent = await create_agent_from_blueprint(
                db,
                blueprint_id=root.id,
                context=own,
                body=CreateAgentFromBlueprintIn(model_id=model.id),
            )
    # Then: rejection has zero executed or staged effects; control persists exact scope.
    if denied:
        assert dml == []
        assert not db.new and not db.dirty
    else:
        tool = await db.scalar(select(Tool).where(Tool.name == "New planned tool"))
        assert tool is not None
        assert (tool.user_id, tool.org_id, tool.tenant_id) == (
            own.user_id,
            own.org_id,
            own.tenant_id,
        )
        assert tool.parameters == {"url": "https://example.invalid"}
        assert tool.id in [link.tool_id for link in agent.tool_links]
        assert root.created_agent_count == 1 and dml


@pytest.mark.parametrize("assignment", [False, True], ids=["create", "assignment"])
@pytest.mark.parametrize("denied", [False, True], ids=["control", "denied"])
async def test_mcp_graph_preflight_when_late_server_missing(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    assignment: bool,
    denied: bool,
) -> None:
    # Given: early MCP creation or assignment, followed by a missing server.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    credential = Credential(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Bound",
        definition_key="mcp_secret",
        data_encrypted="unused",
        key_id="fixture",
        field_keys=[],
        status="active",
    )
    server = McpServer(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Early",
        transport="streamable_http",
        url="https://example.invalid",
        credential_id=None,
    )
    db.add_all([model, credential])
    if assignment:
        db.add(server)
    rows: list[JsonValue] = [
        {"name": "search", "server": {"name": "Early", "url": "https://example.invalid"}}
    ]
    if denied:
        rows.append({"name": "late", "server": {}})
    root = blueprint(own, {"tools": new_tool(), "mcp_tools": rows})
    root.spec["setup"] = {
        "required_credentials": [
            {"key": "mcp_early", "required": True, "definition_key": "mcp_secret"}
        ]
    }
    db.add(root)
    await db.commit()
    body = CreateAgentFromBlueprintIn(
        model_id=model.id, credential_bindings={"mcp_early": credential.id}
    )
    # When: the actual service resolves MCP dependencies without rollback protection.
    with materialization_sql(db) as dml:
        if denied:
            with pytest.raises(AppError) as caught:
                await create_agent_from_blueprint(db, blueprint_id=root.id, context=own, body=body)
            assert caught.value.status == 422
        else:
            await create_agent_from_blueprint(db, blueprint_id=root.id, context=own, body=body)
    # Then: rejected assignment leaves identity-map and persisted rows untouched.
    if denied:
        assert dml == []
        assert not db.new and not db.dirty
        assert server.credential_id is None
    else:
        actual = await db.scalar(select(McpServer).where(McpServer.name == "Early"))
        assert actual is not None and actual.credential_id == credential.id
        tool = await db.scalar(select(McpTool).where(McpTool.server_id == actual.id))
        assert tool is not None and tool.name == "search" and dml
