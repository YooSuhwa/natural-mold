"""Scope-check stdio capability edges even when runtime projection omits them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.authz.runtime_capability_scope import require_runtime_capability_tree
from app.credentials import service as credential_service
from app.exceptions import AppError
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.services.chat.runtime_context import build_tools_config, get_agent_with_tools

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_trigger_mcp_preflight import PeerMcpCase

pytest_plugins = ["tests.authz.test_private_trigger_mcp_preflight"]


@dataclass(frozen=True, slots=True)
class StdioCase:
    mcp: PeerMcpCase
    scopes: PrivateScopes


@pytest.fixture
def stdio_case(peer_mcp_case: PeerMcpCase, private_scopes: PrivateScopes) -> StdioCase:
    return StdioCase(peer_mcp_case, private_scopes)


@pytest.mark.parametrize("edge", ["server_owner", "server_org", "credential"])
async def test_foreign_stdio_edge_denied_before_credentials_and_run_claim(
    db: AsyncSession,
    stdio_case: StdioCase,
    edge: Literal["server_owner", "server_org", "credential"],
) -> None:
    case = stdio_case.mcp
    case.child.user_id = case.context.user_id
    case.server.transport = "stdio"
    case.server.url = None
    case.server.command = "echo"
    case.server.args = ["fixture"]
    match edge:
        case "server_owner":
            pass
        case "server_org":
            case.server.user_id = case.context.user_id
            case.server.org_id = stdio_case.scopes.second.org_id
            case.server.tenant_id = stdio_case.scopes.second.tenant_id
        case "credential":
            case.server.user_id = case.context.user_id
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    db.expunge_all()
    parent = await get_agent_with_tools(db, case.trigger.agent_id, case.context)
    child = await get_agent_with_tools(db, case.child.id, case.context)
    assert parent is not None and child is not None
    with pytest.MonkeyPatch.context() as patches:
        decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        with pytest.raises(AppError) as preflight:
            require_runtime_capability_tree(parent, case.context)
        assert preflight.value.status == 404
        with pytest.raises(AppError) as projection:
            await build_tools_config(child, context=case.context, db=db)
        assert projection.value.status in (404, 422)
        for _ in range(2):
            assert await trigger_executor.execute_trigger(str(case.trigger.id), force=True) is None
        decrypt.assert_not_awaited()
    assert await db.scalar(select(AgentTriggerRun.id)) is None
    trigger = await db.get(AgentTrigger, case.trigger.id, populate_existing=True)
    assert trigger is not None and trigger.failure_count == 0 and trigger.status == "active"
