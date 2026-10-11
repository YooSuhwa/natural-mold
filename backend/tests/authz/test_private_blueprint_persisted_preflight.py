"""Read plans use persisted decisions even when the identity map contains old state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.tool import Tool
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("kind", ["blueprint", "model", "credential"])
async def test_denied_when_persisted_decision_changes_after_caching(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    kind: Literal["blueprint", "model", "credential"],
) -> None:
    # Given: cached accepted state, followed by a real database-only change.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    credential = Credential(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="LLM",
        definition_key="openai_api",
        data_encrypted="unused",
        key_id="fixture",
        status="active",
    )
    root = blueprint(own, {"tools": new_tool()})
    root.spec["setup"] = {
        "required_credentials": [{"key": "llm", "required": True, "definition_key": "openai_api"}]
    }
    db.add_all([model, credential, root])
    await db.commit()
    match kind:
        case "blueprint":
            changed = {**root.spec, "agent": {"middleware_configs": [{"type": "missing"}]}}
            statement = (
                update(AgentBlueprint).where(AgentBlueprint.id == root.id).values(spec=changed)
            )
        case "model":
            statement = update(Model).where(Model.id == model.id).values(is_visible=False)
        case "credential":
            statement = (
                update(Credential)
                .where(Credential.id == credential.id)
                .values(definition_key="mcp_secret")
            )
        case unreachable:
            assert_never(unreachable)
    await db.execute(statement.execution_options(synchronize_session=False))
    await db.commit()
    # When: cached ORM objects remain, but the service must use persisted decisions.
    caught = None
    with materialization_sql(db) as dml:
        try:
            await create_agent_from_blueprint(
                db,
                blueprint_id=root.id,
                context=own,
                body=CreateAgentFromBlueprintIn(
                    model_id=model.id, credential_bindings={"llm": credential.id}
                ),
            )
        except AppError as error:
            caught = error
    # Then: the current rejected state wins before any materialization SQL.
    assert caught is not None, dml
    assert caught.status == (409 if kind == "credential" else 422)
    assert dml == []


@pytest.mark.parametrize("kind", ["tool", "mcp"])
async def test_control_when_cached_candidate_spec_is_outdated(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    kind: Literal["tool", "mcp"],
) -> None:
    # Given: same-name cached candidate no longer matches the persisted spec.
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    root = blueprint(own, {"tools": new_tool()})
    if kind == "tool":
        existing = Tool(
            id=uuid4(),
            user_id=own.user_id,
            org_id=own.org_id,
            tenant_id=own.tenant_id,
            name="New planned tool",
            definition_key="http_request",
            parameters={"url": "https://example.invalid"},
        )
        statement = (
            update(Tool)
            .where(Tool.id == existing.id)
            .values(parameters={"url": "https://changed.invalid"})
        )
    else:
        existing = McpServer(
            id=uuid4(),
            user_id=own.user_id,
            org_id=own.org_id,
            tenant_id=own.tenant_id,
            name="Remote",
            transport="streamable_http",
            url="https://example.invalid",
        )
        root.spec["capabilities"]["mcp_tools"] = [
            {"name": "search", "server": {"name": "Remote", "url": "https://example.invalid"}}
        ]
        statement = (
            update(McpServer)
            .where(McpServer.id == existing.id)
            .values(url="https://changed.invalid")
        )
    db.add_all([root, model, existing])
    await db.commit()
    await db.execute(statement.execution_options(synchronize_session=False))
    await db.commit()
    # When: actual accepted materialization performs structural matching.
    await create_agent_from_blueprint(
        db, blueprint_id=root.id, context=own, body=CreateAgentFromBlueprintIn(model_id=model.id)
    )
    # Then: it creates a matching row rather than reusing an obsolete cached spec.
    if kind == "tool":
        ids = list(await db.scalars(select(Tool.id)))
    else:
        ids = list(await db.scalars(select(McpServer.id)))
    assert len(ids) == 2 and existing.id in ids
