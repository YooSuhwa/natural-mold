"""Scheduled MCP probes reject persisted cross-company credential edges."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.mcp import discovery
from app.models.health_check_history import HealthCheckHistory
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services import health_check, mcp_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("sweep", ["persistent", "concurrent", "lightweight", "discovery"])
async def test_scheduled_health_rejects_foreign_credential_before_probe(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    sweep: Literal["persistent", "concurrent", "lightweight", "discovery"],
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    credential = await credential_service.create(
        db, CredentialCreation("mcp_secret", "Foreign dummy", {"secret": "B06_DUMMY_ONLY"}), foreign
    )
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Own server with malformed foreign credential",
        transport="streamable_http",
        url="https://example.invalid/mcp",
        credential_id=credential.id,
    )
    db.add(server)
    await db.commit()
    resolve = AsyncMock()
    probe = AsyncMock(return_value={"success": True, "tools": [], "error": None})
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(health_check, "resolve_mcp_auth", resolve)
        patches.setattr(health_check.mcp_client, "connect_and_list", probe)
        if sweep != "discovery":
            patches.setattr(mcp_service.mcp_discovery, "test_server", probe)
        patches.setattr(discovery, "resolve_mcp_auth", resolve)
        patches.setattr(discovery, "connect_and_list", probe)
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        match sweep:
            case "discovery":
                with pytest.raises(AppError) as captured:
                    await discovery.test_server(db, server)
                assert captured.value.status == 404
            case "lightweight":
                await mcp_service.poll_mcp_servers_health(session_factory=TestSession)
            case "persistent":
                await health_check.check_all_active(db)
            case "concurrent":
                await health_check.check_all_active(db, session_factory=TestSession)
            case unreachable:
                assert_never(unreachable)
        resolve.assert_not_awaited()
        probe.assert_not_awaited()
        decrypt.assert_not_awaited()
        assert await db.scalar(select(HealthCheckHistory.id)) is None


async def test_probe_uses_persisted_org_then_denies_revoked_membership(
    db: AsyncSession, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    own, other = private_scopes.first, private_scopes.second
    credential = await credential_service.create(
        db, CredentialCreation("mcp_secret", "Owned dummy", {"secret": "B06_DUMMY_ONLY"}), own
    )
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Original company probe",
        transport="streamable_http",
        url="https://example.invalid/mcp",
        credential_id=credential.id,
    )
    db.add(server)
    user = await db.get(User, own.user_id)
    assert user is not None
    user.last_active_org_id = other.org_id
    await db.commit()
    probe = AsyncMock(return_value={"success": True, "tools": [], "error": None})
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    monkeypatch.setattr(discovery, "connect_and_list", probe)
    monkeypatch.setattr(credential_service, "decrypt_with_external", decrypt)
    result = await discovery.test_server(db, server)
    assert result["success"] is True
    probe.assert_awaited_once()
    decrypt.assert_awaited_once()
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
        )
    )
    await db.commit()
    probe.reset_mock()
    decrypt.reset_mock()
    with pytest.raises(AppError):
        await discovery.test_server(db, server)
    probe.assert_not_awaited()
    decrypt.assert_not_awaited()
