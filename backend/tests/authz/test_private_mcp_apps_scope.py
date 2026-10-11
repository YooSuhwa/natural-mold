"""MCP App HTTP commands cannot revive a binding from another selected company."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.mcp.apps_binding_store import McpAppsError, record_runtime_binding
from app.mcp.domain import McpAppRuntimeContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.services import mcp_apps_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class McpAppCase:
    client: AsyncClient
    context: OrgContext
    foreign_org_id: UUID
    conversation_id: UUID
    run_id: UUID


@pytest.fixture
async def mcp_app_case(
    db: AsyncSession, private_scopes: PrivateScopes, raw_client: AsyncClient
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


@pytest.mark.parametrize("method", ["resources/list", "resources/read", "tools/call"])
async def test_foreign_company_app_command_denies_before_proxy(
    mcp_app_case: McpAppCase,
    method: Literal["resources/list", "resources/read", "tools/call"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = mcp_app_case
    token = case.client.cookies.get("moldy_csrf")
    assert token is not None
    headers = {"X-CSRF-Token": token}
    read = AsyncMock(
        return_value={"uri": "ui://fixture/app", "mimeType": "text/html", "html": "", "meta": {}}
    )
    call = AsyncMock(return_value={"content": []})
    monkeypatch.setattr(mcp_apps_service, "read_bound_resource", read)
    monkeypatch.setattr(mcp_apps_service, "call_bound_tool", call)
    params = {"name": "fixture", "arguments": {}} if method == "tools/call" else {}
    if method == "resources/read":
        params = {"uri": "ui://fixture/app"}
    url = f"/api/conversations/{case.conversation_id}/runs/{case.run_id}/mcp-apps/fixture-call"
    own = await case.client.post(url, json={"method": method, "params": params}, headers=headers)
    assert own.status_code == 200
    read.reset_mock()
    call.reset_mock()
    switched = await case.client.post(
        "/api/me/active-org", json={"org_id": str(case.foreign_org_id)}, headers=headers
    )
    assert switched.status_code == 200
    response = await case.client.post(
        url,
        json={"method": method, "params": params},
        headers=headers,
    )
    assert response.status_code == 404
    read.assert_not_awaited()
    call.assert_not_awaited()


@pytest.mark.parametrize("revoked", [False, True])
async def test_detached_mcp_binding_uses_original_scope_and_fresh_membership(
    db: AsyncSession, mcp_app_case: McpAppCase, revoked: bool
) -> None:
    case = mcp_app_case
    binding = await db.scalar(select(McpAppInvocationBinding))
    assert binding is not None
    runtime = McpAppRuntimeContext(
        org_context=case.context,
        conversation_id=case.conversation_id,
        user_id=case.context.user_id,
        agent_id=binding.agent_id,
        credential_subject_user_id=case.context.user_id,
        mcp_server_id=binding.mcp_server_id,
        mcp_tool_id=binding.mcp_tool_id,
        remote_tool_name=binding.remote_tool_name,
        resource_uri=binding.resource_uri,
        tool_meta=binding.tool_meta,
    )
    token = case.client.cookies.get("moldy_csrf")
    assert token is not None
    switched = await case.client.post(
        "/api/me/active-org",
        json={"org_id": str(case.foreign_org_id)},
        headers={"X-CSRF-Token": token},
    )
    assert switched.status_code == 200
    if revoked:
        await db.execute(
            delete(OrganizationMember).where(
                OrganizationMember.org_id == case.context.org_id,
                OrganizationMember.user_id == case.context.user_id,
            )
        )
        await db.commit()
        with pytest.raises(McpAppsError) as captured:
            await record_runtime_binding(
                runtime, str(case.run_id), "detached-call", None, None, session_factory=TestSession
            )
        assert captured.value.status_code == 404
        assert (
            await db.scalar(
                select(McpAppInvocationBinding.id).where(
                    McpAppInvocationBinding.tool_call_id == "detached-call"
                )
            )
            is None
        )
    else:
        recorded = await record_runtime_binding(
            runtime, str(case.run_id), "detached-call", None, None, session_factory=TestSession
        )
        assert recorded is not None
        row = await db.get(McpAppInvocationBinding, UUID(recorded))
        assert row is not None and row.conversation_id == case.conversation_id
