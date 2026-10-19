"""F-07 wiring: when the guard is installed, what its policy holds, where it sits."""

from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.mcp_auth_context import McpAuthContext
from app.agent_runtime.mcp_cache import MCPToolWithRetry
from app.agent_runtime.runtime import connection_guard as guard_module
from app.agent_runtime.runtime import connection_guard_policy as policy_module
from app.agent_runtime.runtime.connection_guard import (
    ConnectionGuardMiddleware,
    with_connection_guard,
)
from app.agent_runtime.runtime.connection_guard_order import (
    HITL_SLOT,
    GuardedApprovalMiddleware,
    order_connection_guard,
)
from app.agent_runtime.runtime.connection_guard_policy import (
    ConnectionGuardPolicy,
    build_connection_guard_policy,
    read_credential_states,
)
from app.authz.principal import Principal
from app.authz.runtime_access_planner import Exclusion
from app.models.resource_credential_binding import ResourceCredentialBinding
from tests.authz.e01_support import add_credential, seed
from tests.authz.f05_support import enforce
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

SERVER_ID, MCP_TOOL_ID, TOOL_ID, CRED = (str(uuid.uuid4()) for _ in range(4))
EXCLUDED_ID = uuid.uuid4()


def _tool(name: str) -> StructuredTool:
    async def run(q: str) -> str:
        return q

    return StructuredTool.from_function(coroutine=run, name=name, description=name)


def _configs() -> list[dict[str, Any]]:
    mcp = {
        "mcp_server_url": "https://mcp.example.com/mcp",
        "mcp_server_id": SERVER_ID,
        "mcp_server_name": "Slack",
        "mcp_tool_id": MCP_TOOL_ID,
        "mcp_tool_name": "post_message",
        "name": "post_message",
        "credential_id": CRED,
        "credential_mode": "per_user",
        "tenant_id": str(TEST_TENANT_ID),
    }
    registry = {"tool_id": TOOL_ID, "name": "Mail Tool", "definition_key": "gmail_send"}
    registry |= {"credential_id": CRED, "tenant_id": str(TEST_TENANT_ID)}
    return [mcp, registry, {"tool_id": "x", "name": "plain", "definition_key": "builtin:x"}]


def _cfg(plan: Any) -> SimpleNamespace:
    return SimpleNamespace(
        tools_config=_configs(),
        access_plan=plan,
        caller_user_id=str(TEST_USER_ID),
        agent_owner_user_id=None,
        user_id=None,
    )


def _plan() -> SimpleNamespace:
    excluded_tool = SimpleNamespace(id=EXCLUDED_ID, name="Excluded Tool", definition_key="http")
    agent = SimpleNamespace(tool_links=[SimpleNamespace(tool=excluded_tool)], mcp_tool_links=[])
    principal = Principal("user", TEST_USER_ID, TEST_ORG_ID)
    return SimpleNamespace(
        agent=agent,
        excluded=(Exclusion("tool", EXCLUDED_ID, "denied"),),
        decided=frozenset({f"mcp_tool:{MCP_TOOL_ID}"}),
        subject=SimpleNamespace(principal=principal),
    )


def _mcp_tool() -> MCPToolWithRetry:
    context = McpAuthContext.from_config(_configs()[0])
    return MCPToolWithRetry(_tool("post_message"), auth_context=context)


def test_legacy_runs_keep_their_middleware_list(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware: list[Any] = [object()]
    monkeypatch.setattr(guard_module, "runtime_access_enforced", lambda: False)
    assert with_connection_guard(middleware, _cfg(_plan()), [], mode="interrupt") is middleware
    monkeypatch.setattr(guard_module, "runtime_access_enforced", lambda: True)
    assert with_connection_guard(middleware, _cfg(None), [], mode="interrupt") is middleware
    (_, guard) = with_connection_guard(middleware, _cfg(_plan()), [], mode="refuse")
    assert isinstance(guard, ConnectionGuardMiddleware) and guard.policy.mode == "refuse"


async def test_the_policy_maps_bound_tools_exclusions_and_fresh_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = f"user:{TEST_USER_ID}"
    monkeypatch.setattr(
        policy_module, "current_authz", lambda: enforce((principal, "can_invoke", "nothing:0"))
    )
    tools = [_mcp_tool(), _tool("Mail_Tool"), _tool("plain")]
    policy = build_connection_guard_policy(_cfg(_plan()), tools, mode="interrupt")

    assert set(policy.guarded) == {"post_message", "Mail_Tool"}
    mcp = policy.guarded["post_message"]
    assert (mcp.resource_type, mcp.resource_name, mcp.credential_mode) == (
        "mcp_server",
        "Slack",
        "per_user",
    )
    assert policy.guarded["Mail_Tool"].resource_type == "tool"
    assert policy.denied == {
        "Excluded Tool": "permission_required",
        "Excluded_Tool": "permission_required",
    }
    assert set(policy.checks) == {"post_message"}  # only rows the plan decided via FGA
    assert await policy.checks["post_message"]() is False  # fresh decision: no tuple
    granted = enforce((principal, "can_invoke", f"mcp_tool:{MCP_TOOL_ID}"))
    monkeypatch.setattr(policy_module, "current_authz", lambda: granted)
    rebuilt = build_connection_guard_policy(_cfg(_plan()), tools, mode="interrupt")
    assert await rebuilt.checks["post_message"]() is True
    assert policy.actor_user_id == str(TEST_USER_ID)


async def test_credential_states_are_read_from_the_database(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(db)
    credential = await add_credential(db, definition_key="mcp_oauth2")
    monkeypatch.setattr(policy_module, "async_session", TestSession)
    tool = policy_module.GuardedTool(
        "post_message", "mcp_server", "Slack", str(credential.id), "shared", str(TEST_TENANT_ID)
    )
    states = await read_credential_states([tool])
    assert states[str(credential.id)].status == "active"
    assert states[str(credential.id)].user_id == str(TEST_USER_ID)

    credential.status = "auth_needed"
    await db.commit()
    assert (await read_credential_states([tool]))[str(credential.id)].status == "auth_needed"
    assert await read_credential_states([replace(tool, credential_id=str(uuid.uuid4()))]) == {}
    assert await read_credential_states([replace(tool, tenant_id=None)]) == {}


async def test_per_user_states_report_whether_the_binding_still_exists(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(db)
    credential = await add_credential(db, definition_key="mcp_oauth2")
    monkeypatch.setattr(policy_module, "async_session", TestSession)
    server_id = uuid.uuid4()
    tool = policy_module.GuardedTool(
        "post_message",
        "mcp_server",
        "Slack",
        str(credential.id),
        "per_user",
        str(TEST_TENANT_ID),
        str(server_id),
        str(TEST_USER_ID),
    )
    assert (await read_credential_states([tool]))[str(credential.id)].bound is False
    db.add(
        ResourceCredentialBinding(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            resource_type="mcp_server",
            resource_id=server_id,
            credential_id=credential.id,
        )
    )
    await db.commit()
    assert (await read_credential_states([tool]))[str(credential.id)].bound is True


def _guard(interactive: bool) -> ConnectionGuardMiddleware:
    mode = "interrupt" if interactive else "refuse"
    return ConnectionGuardMiddleware(ConnectionGuardPolicy(mode=mode))


def test_the_interactive_guard_takes_the_hitl_slot_after_the_approval_step() -> None:
    other, guard = object(), _guard(True)
    ordered = order_connection_guard([guard, other], interrupt_on={"x": True}, permissions=None)

    assert ordered[0] is other
    assert isinstance(ordered[1], GuardedApprovalMiddleware)
    assert isinstance(ordered[1], HumanInTheLoopMiddleware)
    assert ordered[1].interrupt_on.keys() == {"x"}
    assert ordered[2] is guard and guard.name == HITL_SLOT


def test_without_an_approval_step_or_interaction_the_order_is_unchanged() -> None:
    guard = _guard(True)
    assert order_connection_guard([guard], interrupt_on=None, permissions=None) == [guard]
    assert guard.name == "ConnectionGuardMiddleware"
    child = _guard(False)
    assert order_connection_guard([child], interrupt_on={"x": True}, permissions=None) == [child]
