"""Reusable private MCP App graph fixture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes


@dataclass(frozen=True, slots=True)
class McpAppCase:
    client: AsyncClient
    context: OrgContext
    foreign_org_id: UUID
    conversation_id: UUID
    run_id: UUID


@pytest.fixture
async def mcp_app_case(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    raw_client: AsyncClient,
) -> McpAppCase:
    scope = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=scope.user_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        name="App parent",
        system_prompt="Fixture",
        model_id=model.id,
    )
    server = McpServer(
        user_id=scope.user_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        name="Private MCP App",
        transport="streamable_http",
        url="https://example.invalid/mcp",
        status="connected",
    )
    db.add_all([agent, server])
    await db.flush()
    conversation = Conversation(
        user_id=scope.user_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        agent_id=agent.id,
        title="Private App",
        agent_name_snapshot=agent.name,
        last_activity_at=utc_now_naive(),
    )
    tool = McpTool(
        server_id=server.id,
        name="fixture",
        enabled=True,
        metadata_json={"ui": {"resourceUri": "ui://fixture/app"}},
    )
    db.add_all([conversation, tool])
    await db.flush()
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=scope.user_id,
        source="chat",
        status="completed",
        is_active=False,
    )
    db.add_all([run, AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id)])
    await db.flush()
    db.add(
        McpAppInvocationBinding(
            run_id=run.id,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=scope.user_id,
            credential_subject_user_id=scope.user_id,
            mcp_server_id=server.id,
            mcp_tool_id=tool.id,
            tool_call_id="fixture-call",
            remote_tool_name="fixture",
            resource_uri="ui://fixture/app",
            tool_meta={"ui": {"resourceUri": "ui://fixture/app"}},
        )
    )
    await db.commit()
    return McpAppCase(raw_client, scope, private_scopes.second.org_id, conversation.id, run.id)
