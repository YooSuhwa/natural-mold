"""Task 12 list, share, Assistant, and MCP lifecycle cases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.agent_runtime.assistant.tools.write_tools.composition import build_composition_tools
from app.agent_runtime.assistant.tools.write_tools.context import WriteToolContext
from app.exceptions import AppError
from app.mcp import apps_projection_store
from app.mcp.apps_binding_store import McpAppsError, authorize_binding
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.share_link import ShareLink
from app.services import agent_service, mcp_apps_service, share_cache, share_service
from app.services.chat.conversations import create_conversation
from tests.conftest import TestSession
from tests.test_agent_lifecycle_secondary_fences import (
    SecondaryFenceCase,
    _agent,
    secondary_fence_case,
)

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase

pytest_plugins = [
    "tests.authz.org_context_fixture",
    "tests.authz.private_scope_fixture",
    "tests.authz.private_mcp_apps_fixture",
]

__all__ = ["secondary_fence_case"]


async def test_normal_agent_lists_exclude_archived_rows(
    secondary_fence_case: SecondaryFenceCase,
) -> None:
    case = secondary_fence_case
    active = await _agent(case, name="Task 12 active", archived=False)
    archived = await _agent(case, name="Task 12 archived", archived=True)

    listed = await agent_service.list_agents(case.db, case.private_scopes.first)
    summaries = await agent_service.list_agent_summaries(case.db, case.private_scopes.first)

    assert active.id in {row.id for row in listed}
    assert archived.id not in {row.id for row in listed}
    assert active.id in {row["id"] for row in summaries}
    assert archived.id not in {row["id"] for row in summaries}


async def test_archived_agent_cannot_create_or_reuse_share(
    secondary_fence_case: SecondaryFenceCase,
) -> None:
    case = secondary_fence_case
    agent = await _agent(case, name="Task 12 share denied", archived=True)
    archived_at = datetime.now(UTC)
    conversation = Conversation(
        agent_id=agent.id,
        user_id=case.private_scopes.first.user_id,
        org_id=case.private_scopes.first.org_id,
        tenant_id=case.private_scopes.first.tenant_id,
        agent_name_snapshot=agent.name,
        agent_lifecycle_state="archived",
        agent_archived_at_snapshot=archived_at,
        last_activity_at=archived_at,
    )
    case.db.add(conversation)
    await case.db.commit()

    with pytest.raises(AppError) as caught:
        await share_service.create_or_get_active_share(
            case.db,
            conversation,
            case.private_scopes.first,
        )

    assert caught.value.code == "AGENT_ARCHIVED"
    assert await case.db.scalar(select(func.count()).select_from(ShareLink)) == 0


async def test_assistant_composition_writer_denies_archived_root_before_write(
    secondary_fence_case: SecondaryFenceCase,
) -> None:
    case = secondary_fence_case
    agent = await _agent(case, name="Task 12 assistant denied", archived=True)
    tools = {
        tool.name: tool
        for tool in build_composition_tools(
            WriteToolContext(TestSession, agent.id, case.private_scopes.first)
        )
    }

    with pytest.raises(AppError) as caught:
        await tools["add_middleware_to_agent"].ainvoke({"middleware_names": ["summarization"]})

    assert caught.value.code == "AGENT_ARCHIVED"
    persisted = await case.db.get(Agent, agent.id)
    assert persisted is not None and persisted.middleware_configs in (None, [])


async def test_assistant_delegation_writer_denies_archived_child_before_link(
    secondary_fence_case: SecondaryFenceCase,
) -> None:
    case = secondary_fence_case
    parent = await _agent(case, name="Task 12 assistant parent", archived=False)
    child = await _agent(case, name="Task 12 assistant child", archived=True)
    tools = {
        tool.name: tool
        for tool in build_composition_tools(
            WriteToolContext(TestSession, parent.id, case.private_scopes.first)
        )
    }

    with pytest.raises(AppError) as caught:
        await tools["add_subagent_to_agent"].ainvoke({"agent_ids": [str(child.id)]})

    assert caught.value.code == "AGENT_ARCHIVED"
    assert (
        await case.db.scalar(
            select(func.count())
            .select_from(AgentSubAgentLink)
            .where(AgentSubAgentLink.parent_agent_id == parent.id)
        )
        == 0
    )


async def test_share_cache_rechecks_archived_agent_before_cached_snapshot_use(
    secondary_fence_case: SecondaryFenceCase,
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = secondary_fence_case
    agent = await _agent(case, name="Task 12 shared", archived=False)
    conversation = await create_conversation(
        case.db,
        agent.id,
        context=case.private_scopes.first,
    )
    link = await share_service.create_or_get_active_share(
        case.db,
        conversation,
        case.private_scopes.first,
    )
    monkeypatch.setattr(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.routers.shares.chat_secrets.collect_conversation_secret_values",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.routers.shares.trace_storage.get_traces_for_conversation",
        AsyncMock(return_value=[]),
    )
    control = await raw_client.get(f"/api/shares/{link.share_token}")
    assert control.status_code == 200, control.text
    assert share_cache.get_snapshot(link.share_token, conversation.active_branch_checkpoint_id)

    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = case.private_scopes.first.user_id
    await case.db.commit()
    token = link.share_token
    case.db.expunge_all()

    rejected = await raw_client.get(f"/api/shares/{token}")
    assert rejected.status_code == 404
    assert (
        await case.db.scalar(
            select(func.count()).select_from(ShareLink).where(ShareLink.revoked_at.is_(None))
        )
        == 1
    )


async def test_live_mcp_denies_before_secret_and_provider_effects(
    secondary_fence_case: SecondaryFenceCase,
    mcp_app_case: McpAppCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = secondary_fence_case.db
    authorized = await authorize_binding(
        db,
        conversation_id=mcp_app_case.conversation_id,
        run_id=mcp_app_case.run_id,
        tool_call_id="fixture-call",
        context=mcp_app_case.context,
    )
    agent = await db.get(Agent, authorized.run.agent_id)
    assert agent is not None
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = mcp_app_case.context.user_id
    await db.commit()
    auth = AsyncMock()
    wire = AsyncMock()
    monkeypatch.setattr(mcp_apps_service, "_auth_for_context", auth)
    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", wire)

    with pytest.raises(McpAppsError) as caught:
        await mcp_apps_service.read_bound_resource(db, authorized)

    assert caught.value.code == "mcp_app_binding_revoked"
    auth.assert_not_awaited()
    wire.assert_not_awaited()


async def test_static_mcp_projection_remains_readable_when_live_identity_is_null(
    secondary_fence_case: SecondaryFenceCase,
    mcp_app_case: McpAppCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = secondary_fence_case.db
    binding = await db.scalar(select(McpAppInvocationBinding))
    run = await db.get(ConversationRun, mcp_app_case.run_id)
    conversation = await db.get(Conversation, mcp_app_case.conversation_id)
    assert binding is not None and run is not None and conversation is not None
    binding_id = binding.id
    binding.agent_id = None
    run.agent_id = None
    conversation.agent_id = None
    conversation.agent_lifecycle_state = "purged"
    conversation.agent_purged_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    linked = AsyncMock()
    monkeypatch.setattr("app.mcp.apps_binding_store.linked_tool", linked)

    artifact = await apps_projection_store.load_verified_artifact(
        conversation_id=str(mcp_app_case.conversation_id),
        run_id=str(mcp_app_case.run_id),
        tool_call_id="fixture-call",
        binding_id=str(binding_id),
        session_factory=TestSession,
    )
    with pytest.raises(McpAppsError) as caught:
        await authorize_binding(
            db,
            conversation_id=mcp_app_case.conversation_id,
            run_id=mcp_app_case.run_id,
            tool_call_id="fixture-call",
            context=mcp_app_case.context,
        )

    assert artifact is not None
    assert caught.value.code == "mcp_app_not_found"
    linked.assert_not_awaited()
