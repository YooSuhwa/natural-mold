"""Actual persisted MCP capability changes must supersede cached authority."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp import apps_binding_store
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.services import mcp_apps_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_mcp_apps_scope import McpAppCase

pytest_plugins = ["tests.authz.test_private_mcp_apps_scope"]
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


async def authorize(db: AsyncSession, case: McpAppCase) -> apps_binding_store.AuthorizedBinding:
    return await apps_binding_store.authorize_binding(
        db,
        conversation_id=case.conversation_id,
        run_id=case.run_id,
        tool_call_id="fixture-call",
        context=case.context,
    )


@pytest.mark.parametrize("refresh", [False, True])
async def test_persisted_tool_resource_revoke_supersedes_cached_metadata(
    db: AsyncSession, mcp_app_case: McpAppCase, refresh: bool
) -> None:
    # Given: a real valid private MCP App graph is cached in the normal Session.
    assert Path(apps_binding_store.__file__).is_relative_to(SOURCE_ROOT)
    current = await authorize(db, mcp_app_case)
    tool = current.origin_tool
    await db.execute(
        update(McpTool)
        .where(McpTool.id == tool.id)
        .values(metadata_json={"ui": {"resourceUri": "ui://revoked/replacement"}})
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    stored = await db.scalar(select(McpTool.metadata_json).where(McpTool.id == tool.id))
    assert stored == {"ui": {"resourceUri": "ui://revoked/replacement"}}
    if refresh:
        await db.refresh(tool)
    denied: str | None = None
    # When: the real public authorizer reads the now-stale invocation.
    try:
        await authorize(db, mcp_app_case)
    except apps_binding_store.McpAppsError as exc:
        denied = exc.code
    await db.rollback()
    # Then: persisted metadata invalidates the binding even with cached objects.
    assert denied == "mcp_app_context_stale"


@pytest.mark.parametrize("refresh", [False, True])
async def test_persisted_foreign_credential_parent_denies_before_wire_effect(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    mcp_app_case: McpAppCase,
    monkeypatch: pytest.MonkeyPatch,
    refresh: bool,
) -> None:
    # Given: cached server has no Credential; persisted server now references B.
    assert Path(mcp_apps_service.__file__).is_relative_to(SOURCE_ROOT)
    current = await authorize(db, mcp_app_case)
    foreign = private_scopes.second
    credential = Credential(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign dummy credential",
        definition_key="mcp_secret",
        data_encrypted="unused-dummy-cipher",
        key_id="f4-dummy-unused-key",
        field_keys=[],
    )
    db.add(credential)
    await db.commit()
    credential_id = credential.id
    server = current.server
    await db.execute(
        update(McpServer)
        .where(McpServer.id == server.id)
        .values(credential_id=credential_id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    stored = await db.scalar(select(McpServer.credential_id).where(McpServer.id == server.id))
    assert stored == credential_id
    if refresh:
        await db.refresh(server)
    wire = AsyncMock(return_value={"success": False})
    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", wire)
    denied: str | None = None
    # When: production reauthorization and resource-read pipeline are invoked.
    try:
        returned = await authorize(db, mcp_app_case)
        await mcp_apps_service.read_bound_resource(db, returned)
    except apps_binding_store.McpAppsError as exc:
        denied = exc.code
    calls = wire.await_count
    await db.rollback()
    # Then: foreign private Credential parent is rejected before wire boundary.
    assert calls == 0
    assert denied == "mcp_app_auth_unavailable"
