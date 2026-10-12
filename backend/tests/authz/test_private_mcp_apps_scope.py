"""MCP App HTTP commands cannot revive a binding from another selected company."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp import apps_projection_store
from app.mcp.apps_binding_store import (
    McpAppsError,
    authorize_binding,
    record_runtime_binding,
)
from app.mcp.domain import McpAppRuntimeContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.organization import OrganizationMember
from app.services import mcp_apps_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase

pytest_plugins = [
    "tests.authz.private_scope_fixture",
    "tests.authz.private_mcp_apps_fixture",
]


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
    if binding.agent_id is None:
        pytest.fail("active MCP App fixture must remain Agent-bound")
    binding_agent_id = binding.agent_id
    runtime = McpAppRuntimeContext(
        org_context=case.context,
        conversation_id=case.conversation_id,
        user_id=case.context.user_id,
        agent_id=binding_agent_id,
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


async def test_live_mcp_denies_archived_agent_before_auth_or_wire_effects(
    db: AsyncSession,
    mcp_app_case: McpAppCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = mcp_app_case
    authorized = await authorize_binding(
        db,
        conversation_id=case.conversation_id,
        run_id=case.run_id,
        tool_call_id="fixture-call",
        context=case.context,
    )
    agent = await db.get(Agent, authorized.run.agent_id)
    assert agent is not None
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = case.context.user_id
    await db.commit()
    auth = AsyncMock()
    resource_wire = AsyncMock()
    tool_wire = AsyncMock()
    monkeypatch.setattr(mcp_apps_service, "_auth_for_context", auth)
    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", resource_wire)
    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", tool_wire)

    with pytest.raises(McpAppsError) as resource_error:
        await mcp_apps_service.read_bound_resource(db, authorized)
    with pytest.raises(McpAppsError) as tool_error:
        await mcp_apps_service.call_bound_tool(
            db,
            authorized,
            tool_name="fixture",
            arguments={},
        )

    assert resource_error.value.code == "mcp_app_binding_revoked"
    assert tool_error.value.code == "mcp_app_binding_revoked"
    auth.assert_not_awaited()
    resource_wire.assert_not_awaited()
    tool_wire.assert_not_awaited()


async def test_static_mcp_projection_survives_null_historical_agent_identity(
    db: AsyncSession,
    mcp_app_case: McpAppCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = mcp_app_case
    binding = await db.scalar(select(McpAppInvocationBinding))
    run = await db.get(ConversationRun, case.run_id)
    conversation = await db.get(Conversation, case.conversation_id)
    assert binding is not None and run is not None and conversation is not None
    binding_id = binding.id
    binding.agent_id = None
    run.agent_id = None
    conversation.agent_id = None
    conversation.agent_lifecycle_state = "purged"
    conversation.agent_purged_at = utc_now_naive()
    await db.commit()
    linked = AsyncMock()
    monkeypatch.setattr("app.mcp.apps_binding_store.linked_tool", linked)

    artifact = await apps_projection_store.load_verified_artifact(
        conversation_id=str(case.conversation_id),
        run_id=str(case.run_id),
        tool_call_id="fixture-call",
        binding_id=str(binding_id),
        session_factory=TestSession,
    )
    with pytest.raises(McpAppsError) as caught:
        await authorize_binding(
            db,
            conversation_id=case.conversation_id,
            run_id=case.run_id,
            tool_call_id="fixture-call",
            context=case.context,
        )

    assert artifact is not None
    assert artifact["mcp_app"]["binding_id"] == str(binding_id)
    assert caught.value.code == "mcp_app_not_found"
    linked.assert_not_awaited()
