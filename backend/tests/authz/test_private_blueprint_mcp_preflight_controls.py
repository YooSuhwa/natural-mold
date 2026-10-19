"""A rejected complete dependency graph never stages or executes graph mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.tool import Tool
from app.services.agent_blueprint_graph import apply_graph, plan_graph
from app.services.agent_blueprint_graph_types import GraphRequest
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("strategy", ["always_new", "install_missing", "reuse_existing"])
async def test_mcp_control_when_strategy_selects_same_request_candidates(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    strategy: Literal["always_new", "install_missing", "reuse_existing"],
) -> None:
    # Given: two named tools reference the same exact MCP server spec.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    existing = McpServer(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Shared",
        transport="streamable_http",
        url="https://example.invalid",
        args=[],
        env_vars={},
        headers={},
    )
    old_tool = McpTool(id=uuid4(), server_id=existing.id, name="first", enabled=True)
    db.add(model)
    if strategy == "reuse_existing":
        db.add_all([existing, old_tool])
    rows: list[JsonValue] = [
        {"name": name, "server": {"name": "Shared", "url": "https://example.invalid"}}
        for name in ["first", "second"]
    ]
    root = blueprint(own, {"mcp_tools": rows})
    db.add(root)
    await db.commit()
    # When: accepted full service executes the chosen strategy.
    with materialization_sql(db) as dml:
        agent = await create_agent_from_blueprint(
            db,
            blueprint_id=root.id,
            context=own,
            body=CreateAgentFromBlueprintIn(model_id=model.id, dependency_strategy=strategy),
        )
    # Then: always_new preserves per-row creation; other strategies share a server.
    servers = (await db.scalars(select(McpServer))).all()
    tools = (await db.scalars(select(McpTool))).all()
    assert len(servers) == (2 if strategy == "always_new" else 1)
    assert len(tools) == 2
    assert {t.name for t in tools} == {"first", "second"}
    assert {link.mcp_tool_id for link in agent.mcp_tool_links} == {t.id for t in tools}
    assert all(
        (s.user_id, s.org_id, s.tenant_id) == (own.user_id, own.org_id, own.tenant_id)
        for s in servers
    )
    if strategy == "reuse_existing":
        assert old_tool.id in {t.id for t in tools}
    assert dml


async def test_mcp_plan_control_when_repeated_tool_uses_preallocated_id(
    db: AsyncSession,
    private_scopes: PrivateScopes,
) -> None:
    # Given: repeated tool rows formerly observed the first flushed Tool/server.
    rows: list[JsonValue] = [{"name": "repeated", "server": {"name": "Shared"}}] * 2
    request = GraphRequest(
        {"mcp_tools": rows}, private_scopes.first, CreateAgentFromBlueprintIn(), {}
    )
    # When: plan remains effect free; apply uses the final proposed IDs.
    with materialization_sql(db) as dml, db.no_autoflush:
        plan = await plan_graph(db, request)
        assert dml == [] and not db.new and not db.dirty
        assert plan.mcp.ids == (plan.mcp.tools[0].id, plan.mcp.tools[0].id)
        assert len(plan.mcp.servers) == len(plan.mcp.tools) == 1
        await apply_graph(db, plan)
    # Then: planned IDs are exactly the persisted rows after acceptance.
    assert await db.get(McpServer, plan.mcp.servers[0].id) is not None
    assert await db.get(McpTool, plan.mcp.tools[0].id) is not None


async def test_denied_graph_when_unrelated_changes_are_staged(
    db: AsyncSession,
    private_scopes: PrivateScopes,
) -> None:
    # Given: persisted valid root and separate unsaved new/dirty rows.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    root = blueprint(own, {"tools": new_tool(), "skills": [{"slug": "missing"}]})
    db.add_all([model, root])
    await db.commit()
    pending = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Unrelated pending",
        definition_key="http_request",
        parameters={},
    )
    db.add(pending)
    model.display_name = "Unrelated dirty"
    # When: full graph fails after its early proposed Tool.
    with materialization_sql(db) as dml, pytest.raises(AppError):
        await create_agent_from_blueprint(
            db,
            blueprint_id=root.id,
            context=own,
            body=CreateAgentFromBlueprintIn(model_id=model.id),
        )
    # Then: no implicit flush; unrelated pending state survives untouched.
    assert dml == []
    assert list(db.new) == [pending]
    assert list(db.dirty) == [model]
    assert pending.id is None and model.display_name == "Unrelated dirty"


async def test_mcp_control_when_legacy_skip_and_payload_defaults_apply(
    db: AsyncSession,
    private_scopes: PrivateScopes,
) -> None:
    # Given: skipped rows, native list conversion and arbitrary JSON schema payload.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    root = blueprint(
        own,
        {
            "mcp_tools": [
                0,
                {"name": ""},
                {
                    "name": "search",
                    "server": {
                        "name": "Native",
                        "args": "abc",
                        "env_vars": [["STATIC", "fixture"]],
                        "headers": [["X-Mode", "test"]],
                    },
                    "input_schema": ["preserved-json"],
                },
            ]
        },
    )
    db.add_all([model, root])
    await db.commit()
    # When: the complete real materialization accepts the historically supported input.
    await create_agent_from_blueprint(
        db, blueprint_id=root.id, context=own, body=CreateAgentFromBlueprintIn(model_id=model.id)
    )
    # Then: native list conversion and payload defaults are preserved exactly.
    server = await db.scalar(select(McpServer).where(McpServer.name == "Native"))
    assert server is not None and server.args == ["a", "b", "c"]
    assert server.env_vars == {"STATIC": "fixture"} and server.headers == {"X-Mode": "test"}
    tool = await db.scalar(select(McpTool).where(McpTool.server_id == server.id))
    assert tool is not None and tool.input_schema == ["preserved-json"] and tool.enabled
