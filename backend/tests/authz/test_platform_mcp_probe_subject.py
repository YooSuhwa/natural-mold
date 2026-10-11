"""Platform credential authentication uses its validated subject, not creator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.platform_scope import PlatformScope
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.mcp import discovery
from app.models.mcp_server import McpServer
from app.services import health_check

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("method", ["persistent", "discovery"])
async def test_platform_probe_keeps_creator_fk_and_uses_platform_auth_subject(
    db: AsyncSession, private_scopes: PrivateScopes, method: Literal["persistent", "discovery"]
) -> None:
    credential = await credential_service.create(
        db,
        CredentialCreation("mcp_secret", "Platform dummy", {"secret": "B06_DUMMY_ONLY"}),
        PlatformScope(),
    )
    server = McpServer(
        user_id=private_scopes.first.user_id,
        org_id=None,
        tenant_id=None,
        is_system=True,
        name="Platform probe",
        transport="streamable_http",
        url="https://example.invalid/mcp",
        credential_id=credential.id,
    )
    db.add(server)
    await db.commit()
    with pytest.MonkeyPatch.context() as patches:
        probe = AsyncMock(return_value={"success": True, "tools": [], "server_info": {}})
        decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
        patches.setattr(health_check.mcp_client, "connect_and_list", probe)
        patches.setattr(discovery, "connect_and_list", probe)
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        match method:
            case "persistent":
                result = await health_check.check_mcp_server(
                    db, server=server, credential=credential
                )
                assert result.status == "healthy"
            case "discovery":
                response = await discovery.test_server(db, server)
                assert response["success"] is True
            case unreachable:
                assert_never(unreachable)
        probe.assert_awaited_once()
        decrypt.assert_awaited_once()
        assert probe.await_args is not None
        assert probe.await_args.kwargs["credentials"] == {"secret": "B06_DUMMY_ONLY"}
