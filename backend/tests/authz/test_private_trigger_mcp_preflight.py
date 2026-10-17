"""Malformed peer capability edges must fail before private MCP decryption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.authz.org_context import OrgContext
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.organization import OrganizationMember, TenantMember
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.services.chat.runtime_context import build_tools_config, get_agent_with_tools
from app.services.chat.trigger_blocked_tools import trigger_blocked_tools_for_agent_tree

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_trigger_scope"]


@dataclass(frozen=True, slots=True)
class PeerMcpCase:
    trigger: AgentTrigger
    context: OrgContext
    child: Agent
    server: McpServer


@pytest.fixture
async def peer_mcp_case(
    db: AsyncSession, private_trigger: AgentTrigger, private_scopes: PrivateScopes
) -> PeerMcpCase:
    context = private_scopes.first
    peer = User(id=uuid4(), email="trigger-peer@example.com", name="Peer")
    db.add(peer)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=context.tenant_id, user_id=peer.id),
            OrganizationMember(tenant_id=context.tenant_id, org_id=context.org_id, user_id=peer.id),
        ]
    )
    await db.commit()
    peer_context = OrgContext(context.org_id, context.tenant_id, peer.id)
    credential = await credential_service.create(
        db,
        CredentialCreation("mcp_secret", "Peer fixture", {"secret": "B06_DUMMY_ONLY"}),
        peer_context,
    )
    parent_credential = await credential_service.create(
        db,
        CredentialCreation("mcp_secret", "Parent fixture", {"secret": "B06_DUMMY_ONLY"}),
        context,
    )
    parent_tool = Tool(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        credential_id=parent_credential.id,
        name="Owned trigger tool",
        definition_key="builtin:current_datetime",
        parameters={},
    )
    db.add(parent_tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=private_trigger.agent_id, tool_id=parent_tool.id))
    child = Agent(
        user_id=peer.id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Peer child",
        system_prompt="Fixture",
        identity_mode="fixed",
        model_id=await db.scalar(
            select(Agent.model_id).where(Agent.id == private_trigger.agent_id)
        ),
    )
    server = McpServer(
        user_id=peer.id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Peer MCP",
        transport="streamable_http",
        url="https://example.invalid/mcp",
        credential_id=credential.id,
        headers={},
    )
    db.add_all([child, server])
    await db.flush()
    tool = McpTool(server_id=server.id, name="dangerous_fixture", input_schema={})
    db.add(tool)
    await db.flush()
    db.add_all(
        [
            AgentSubAgentLink(parent_agent_id=private_trigger.agent_id, sub_agent_id=child.id),
            AgentMcpToolLink(agent_id=child.id, mcp_tool_id=tool.id),
        ]
    )
    await db.commit()
    return PeerMcpCase(private_trigger, context, child, server)


@pytest.mark.parametrize("edge", ["peer_child", "peer_server"])
async def test_trigger_preflight_rejects_peer_before_secret_or_claim(
    db: AsyncSession, peer_mcp_case: PeerMcpCase, edge: Literal["peer_child", "peer_server"]
) -> None:
    case = peer_mcp_case
    if edge == "peer_server":
        case.child.user_id = case.context.user_id
        await db.commit()
    db.expunge_all()
    parent = await get_agent_with_tools(db, case.trigger.agent_id, case.context)
    assert parent is not None
    with pytest.MonkeyPatch.context() as patches:
        decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        with pytest.raises(AppError) as captured:
            await trigger_blocked_tools_for_agent_tree(parent, db=db, context=case.context)
        assert captured.value.status == 404
        decrypt.assert_not_awaited()
        for _ in range(2):
            assert await trigger_executor.execute_trigger(str(case.trigger.id), force=True) is None
        decrypt.assert_not_awaited()
    assert await db.scalar(select(AgentTriggerRun.id)) is None
    trigger = await db.get(AgentTrigger, case.trigger.id, populate_existing=True)
    assert trigger is not None and trigger.status == "active" and trigger.failure_count == 0


async def test_platform_mcp_keeps_required_creator_and_explicit_null_scope(
    db: AsyncSession, peer_mcp_case: PeerMcpCase
) -> None:
    case = peer_mcp_case
    case.child.user_id = case.context.user_id
    case.server.is_system = True
    case.server.org_id = None
    case.server.tenant_id = None
    case.server.credential_id = None
    await db.commit()
    db.expunge_all()
    child = await get_agent_with_tools(db, case.child.id, case.context)
    assert child is not None
    # m83 deliberately retains a required creator FK on platform MCP rows.
    assert case.server.user_id != case.context.user_id
    configs = await build_tools_config(child, context=case.context, db=db)
    assert len(configs) == 1 and configs[0]["mcp_server_id"] == str(case.server.id)
    assert configs[0]["credentials"] is None
