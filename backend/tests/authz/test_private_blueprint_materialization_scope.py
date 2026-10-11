"""Blueprint dependencies are materialized in the blueprint's selected organization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.model import Model
from app.models.tool import Tool
from app.services.agent_blueprint_service import create_agent_from_blueprint

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


async def test_blueprint_does_not_reuse_same_owner_tool_from_other_company(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    first, second = private_scopes.first, private_scopes.second
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    foreign = Tool(
        user_id=first.user_id,
        org_id=first.org_id,
        tenant_id=first.tenant_id,
        name="Collision fixture",
        definition_key="http_request",
        parameters={"url": "https://example.test/fixture"},
    )
    blueprint = AgentBlueprint(
        user_id=second.user_id,
        org_id=second.org_id,
        tenant_id=second.tenant_id,
        name="Scoped blueprint",
        spec_hash="a" * 64,
        spec={
            "schema_version": 1,
            "resource": "agent_blueprint",
            "agent": {"name": "Scoped blueprint", "system_prompt": "Fixture"},
            "capabilities": {
                "tools": [
                    {
                        "name": foreign.name,
                        "definition_key": foreign.definition_key,
                        "parameters": foreign.parameters,
                    }
                ]
            },
            "setup": {"required_credentials": []},
        },
    )
    db.add_all([model, foreign, blueprint])
    await db.commit()
    agent = await create_agent_from_blueprint(
        db,
        blueprint_id=blueprint.id,
        context=second,
        body=CreateAgentFromBlueprintIn(model_id=model.id),
    )
    assert (agent.user_id, agent.org_id, agent.tenant_id) == (
        second.user_id,
        second.org_id,
        second.tenant_id,
    )
    tools = list((await db.scalars(select(Tool))).all())
    own = [tool for tool in tools if tool.org_id == second.org_id]
    assert len(own) == 1 and own[0].id != foreign.id
    assert own[0].user_id == second.user_id and own[0].tenant_id == second.tenant_id
