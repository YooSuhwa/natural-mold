"""Actual m83 blueprint materialization persists Agent, Tool and MCP scope together."""

from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.tool import Tool
from app.models.user import User
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_blueprint_dependencies_share_exact_parent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-blueprint@example.com",
                "name": "Blueprint fixture",
                "password": "native-blueprint-password",
            },
        )
        assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-blueprint@example.com"))
            assert user is not None
            context = await resolve_org_context(
                db,
                CurrentUser(id=user.id, email=user.email, name=user.name),
                cookie=str(user.last_active_org_id),
            )
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            blueprint = AgentBlueprint(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Native blueprint",
                spec_hash="a" * 64,
                spec={
                    "agent": {"name": "Native blueprint", "system_prompt": "Fixture"},
                    "capabilities": {
                        "tools": [
                            {
                                "name": "Native HTTP",
                                "definition_key": "http_request",
                                "parameters": {"url": "https://example.test/fixture"},
                            }
                        ],
                        "mcp_tools": [
                            {
                                "name": "native_tool",
                                "input_schema": {"type": "object"},
                                "server": {
                                    "name": "Native MCP",
                                    "transport": "streamable_http",
                                    "url": "https://example.test/mcp",
                                },
                            }
                        ],
                    },
                    "setup": {"required_credentials": []},
                },
            )
            db.add_all([model, blueprint])
            await db.commit()
            agent = await create_agent_from_blueprint(
                db,
                blueprint_id=blueprint.id,
                context=context,
                body=CreateAgentFromBlueprintIn(model_id=model.id),
            )
            await db.commit()
            tools = list((await db.scalars(select(Tool))).all())
            servers = list((await db.scalars(select(McpServer))).all())
            assert len(tools) == len(servers) == 1
            for resource in [agent, *tools, *servers]:
                assert (resource.user_id, resource.org_id, resource.tenant_id) == (
                    context.user_id,
                    context.org_id,
                    context.tenant_id,
                )
            assert isinstance(agent.id, UUID)
