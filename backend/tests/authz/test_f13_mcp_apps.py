"""F-13: MCP Apps commands re-decide on every call (plan 11.0 "MCP Apps").

``mcp_tool#can_invoke`` is asked per command (``authorize_binding``) and the caller's
own connection is re-resolved per call, so revoking either refuses the next call
before anything reaches the MCP server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.mcp.auth import ResolvedMcpAuth
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.resource_credential_binding import ResourceCredentialBinding
from app.services import mcp_apps_service

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase

pytest_plugins = [
    "tests.authz.private_scope_fixture",
    "tests.authz.private_mcp_apps_fixture",
]


def _enforce(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str, str]) -> None:
    fake = FakeAuthz(allowed=frozenset(allowed))
    service = AuthzService(fake, mode="enforce", enforce_types=frozenset({"mcp_tool"}))
    monkeypatch.setattr("app.authz.runtime._current", service)


async def _call(case: McpAppCase):
    token = case.client.cookies.get("moldy_csrf")
    url = f"/api/conversations/{case.conversation_id}/runs/{case.run_id}/mcp-apps/fixture-call"
    command = {"method": "tools/call", "params": {"name": "fixture", "arguments": {}}}
    return await case.client.post(url, json=command, headers={"X-CSRF-Token": token or ""})


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    call = AsyncMock(return_value={"success": True, "content": []})
    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", call)
    auth = AsyncMock(return_value=ResolvedMcpAuth(headers={}, credentials={}))
    monkeypatch.setattr(mcp_apps_service, "resolve_mcp_auth", auth)
    return call


async def test_mcp_app_call_refuses_after_can_invoke_is_revoked(
    db: AsyncSession, mcp_app_case: McpAppCase, wire: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_id = await db.scalar(select(McpTool.id).where(McpTool.name == "fixture"))
    me, obj = f"user:{mcp_app_case.context.user_id}", f"mcp_tool:{tool_id}"
    _enforce(monkeypatch, (me, "can_invoke", obj))
    assert (await _call(mcp_app_case)).status_code == 200
    wire.assert_awaited_once()

    _enforce(monkeypatch)  # can_invoke revoked
    denied = await _call(mcp_app_case)
    assert (denied.status_code, denied.json()["error"]["code"]) == (
        403,
        "mcp_app_binding_revoked",
    )
    wire.assert_awaited_once()


async def test_mcp_app_call_refuses_after_the_connection_is_removed(
    db: AsyncSession, mcp_app_case: McpAppCase, wire: AsyncMock
) -> None:
    ctx = mcp_app_case.context
    credential = await credential_service.create(
        db, CredentialCreation(definition_key="http_bearer", name="c", data={"token": "x"}), ctx
    )
    server = await db.scalar(select(McpServer))
    assert server is not None
    server.credential_mode, server.credential_definition_key = "per_user", "http_bearer"
    db.add(
        ResourceCredentialBinding(
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            resource_type="mcp_server",
            resource_id=server.id,
            credential_id=credential.id,
        )
    )
    await db.commit()
    assert (await _call(mcp_app_case)).status_code == 200
    wire.assert_awaited_once()

    await db.execute(delete(ResourceCredentialBinding))  # the caller's connection removed
    await db.commit()
    denied = await _call(mcp_app_case)
    assert (denied.status_code, denied.json()["error"]["code"]) == (
        409,
        "credential_binding_required",
    )
    wire.assert_awaited_once()
