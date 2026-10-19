"""The wizard preview checks selected organization before sending a secret."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import resolve_org_context
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.organization import OrganizationMember
from app.routers import mcp

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_wizard_probe_denies_foreign_and_revoked_credential_before_effects(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    org = org_fixture
    context = await resolve_org_context(db, org.user, cookie=str(org.first))
    credential = await credential_service.create(
        db, CredentialCreation("mcp_secret", "Wizard dummy", {"secret": "B06_DUMMY_ONLY"}), context
    )
    await db.commit()
    payload = {
        "transport": "streamable_http",
        "url": "https://example.invalid/mcp",
        "credential_id": str(credential.id),
    }
    with pytest.MonkeyPatch.context() as patches:
        probe = AsyncMock(return_value={"success": True, "tools": [], "server_info": {}})
        decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
        patches.setattr(mcp, "connect_and_list", probe)
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        own = await raw_client.post(
            "/api/mcp-servers/probe", json=payload, headers=org.session.headers()
        )
        assert own.status_code == 200
        probe.assert_awaited_once()
        decrypt.assert_awaited_once()
        switched = await raw_client.post(
            "/api/me/active-org", json={"org_id": str(org.second)}, headers=org.session.headers()
        )
        assert switched.status_code == 200
        probe.reset_mock()
        decrypt.reset_mock()
        foreign = await raw_client.post(
            "/api/mcp-servers/probe", json=payload, headers=org.session.headers()
        )
        assert foreign.status_code == 404
        await db.execute(
            delete(OrganizationMember).where(
                OrganizationMember.org_id == org.first, OrganizationMember.user_id == org.user.id
            )
        )
        await db.commit()
        revoked = await raw_client.post(
            "/api/mcp-servers/probe", json=payload, headers=org.session.headers()
        )
        assert revoked.status_code == 404
        probe.assert_not_awaited()
        decrypt.assert_not_awaited()
