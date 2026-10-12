"""Native PostgreSQL proofs for secondary Agent lifecycle fences."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.mcp import apps_projection_store
from app.mcp.apps_binding_store import McpAppsError, authorize_binding
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.services import agent_blueprint_graph, mcp_apps_service
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool


async def test_native_blueprint_archived_child_denies_before_graph_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        context = OrgContext(
            tenant_id=fixture.actors.tenant_id,
            org_id=fixture.actors.org_id,
            user_id=fixture.actors.owner_two_id,
        )
        async with fixture.sessions() as db:
            active = await db.get(Agent, graph.child_agent_ids[1])
            archived = await db.get(Agent, graph.child_agent_ids[0])
            assert active is not None and archived is not None
            model = Model(
                provider="openai_compatible",
                model_name=f"task-12-visible-{uuid4().hex}",
                display_name="Task 12 visible model",
                base_url="https://example.invalid/v1",
                is_visible=True,
                source="manual",
            )
            db.add(model)
            await db.flush()
            active_blueprint = blueprint(
                context,
                {"tools": new_tool(), "subagents": [{"name": active.name}]},
            )
            archived_blueprint = blueprint(
                context,
                {"tools": new_tool(), "subagents": [{"name": archived.name}]},
            )
            archived_blueprint.name = "Archived child blueprint"
            archived_blueprint.spec_hash = "a" * 64
            archived.archived_at = datetime.now(UTC).replace(tzinfo=None)
            archived.archived_by = context.user_id
            db.add_all([active_blueprint, archived_blueprint])
            await db.commit()
            model_id = model.id

            plan_tools = AsyncMock(wraps=agent_blueprint_graph.plan_tools)
            resolve_skills = AsyncMock(wraps=agent_blueprint_graph.resolve_skills)
            plan_mcp = AsyncMock(wraps=agent_blueprint_graph.plan_mcp)
            monkeypatch.setattr(agent_blueprint_graph, "plan_tools", plan_tools)
            monkeypatch.setattr(agent_blueprint_graph, "resolve_skills", resolve_skills)
            monkeypatch.setattr(agent_blueprint_graph, "plan_mcp", plan_mcp)

            control = await create_agent_from_blueprint(
                db,
                blueprint_id=active_blueprint.id,
                context=context,
                body=CreateAgentFromBlueprintIn(model_id=model_id),
            )
            await db.commit()
            assert {link.sub_agent_id for link in control.sub_agent_links} == {active.id}
            plan_tools.reset_mock()
            resolve_skills.reset_mock()
            plan_mcp.reset_mock()
            before = await db.scalar(select(func.count()).select_from(Agent))

            with materialization_sql(db) as dml, pytest.raises(AppError) as caught:
                await create_agent_from_blueprint(
                    db,
                    blueprint_id=archived_blueprint.id,
                    context=context,
                    body=CreateAgentFromBlueprintIn(model_id=model_id),
                )

            assert caught.value.code == "AGENT_ARCHIVED"
            plan_tools.assert_not_awaited()
            resolve_skills.assert_not_awaited()
            plan_mcp.assert_not_awaited()
            assert dml == []
            assert not db.new and not db.dirty
            assert await db.scalar(select(func.count()).select_from(Agent)) == before
            assert fixture.sentinel.provider_invocations == 0


async def test_native_live_mcp_denial_preserves_static_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        context = OrgContext(
            tenant_id=fixture.actors.tenant_id,
            org_id=fixture.actors.org_id,
            user_id=fixture.actors.owner_two_id,
        )
        async with fixture.sessions() as db:
            agent = await db.get(Agent, fixture.graph.child_agent_ids[0])
            assert agent is not None
            server = McpServer(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Task 12 native MCP",
                transport="streamable_http",
                url="https://example.invalid/mcp",
                status="connected",
            )
            conversation = Conversation(
                id=uuid4(),
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                agent_id=agent.id,
                title="Task 12 native MCP",
                agent_name_snapshot=agent.name,
                agent_lifecycle_state="active",
                last_activity_at=datetime.now(UTC).replace(tzinfo=None),
            )
            tool = McpTool(
                server=server,
                name="fixture",
                enabled=True,
                metadata_json={"ui": {"resourceUri": "ui://task-12/native"}},
            )
            db.add_all([server, conversation, tool])
            await db.flush()
            run = ConversationRun(
                conversation_id=conversation.id,
                agent_id=agent.id,
                user_id=context.user_id,
                source="chat",
                status="completed",
                is_active=False,
            )
            db.add_all([run, AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id)])
            await db.flush()
            binding = McpAppInvocationBinding(
                run_id=run.id,
                conversation_id=conversation.id,
                agent_id=agent.id,
                user_id=context.user_id,
                credential_subject_user_id=context.user_id,
                mcp_server_id=server.id,
                mcp_tool_id=tool.id,
                tool_call_id="task-12-native-call",
                remote_tool_name=tool.name,
                resource_uri="ui://task-12/native",
                tool_meta={"ui": {"resourceUri": "ui://task-12/native"}},
            )
            db.add(binding)
            await db.commit()
            authorized = await authorize_binding(
                db,
                conversation_id=binding.conversation_id,
                run_id=binding.run_id,
                tool_call_id=binding.tool_call_id,
                context=context,
            )
            agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
            agent.archived_by = context.user_id
            await db.commit()
            auth = AsyncMock()
            wire = AsyncMock()
            monkeypatch.setattr(mcp_apps_service, "_auth_for_context", auth)
            monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", wire)

            with pytest.raises(McpAppsError) as archived_error:
                await mcp_apps_service.read_bound_resource(db, authorized)

            artifact = await apps_projection_store.load_verified_artifact(
                conversation_id=str(binding.conversation_id),
                run_id=str(binding.run_id),
                tool_call_id=binding.tool_call_id,
                binding_id=str(binding.id),
                session_factory=fixture.sessions,
            )
            assert artifact is not None
            assert archived_error.value.code == "mcp_app_binding_revoked"
            auth.assert_not_awaited()
            wire.assert_not_awaited()

            run = await db.get(ConversationRun, binding.run_id)
            assert run is not None
            binding.agent_id = None
            run.agent_id = None
            conversation.agent_id = None
            conversation.agent_lifecycle_state = "purged"
            conversation.agent_purged_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            linked = AsyncMock()
            monkeypatch.setattr("app.mcp.apps_binding_store.linked_tool", linked)

            retained = await apps_projection_store.load_verified_artifact(
                conversation_id=str(binding.conversation_id),
                run_id=str(binding.run_id),
                tool_call_id=binding.tool_call_id,
                binding_id=str(binding.id),
                session_factory=fixture.sessions,
            )
            with pytest.raises(McpAppsError) as null_error:
                await authorize_binding(
                    db,
                    conversation_id=binding.conversation_id,
                    run_id=binding.run_id,
                    tool_call_id=binding.tool_call_id,
                    context=context,
                )

            assert retained is not None
            assert null_error.value.code == "mcp_app_not_found"
            linked.assert_not_awaited()
            assert fixture.sentinel.provider_invocations == 0
