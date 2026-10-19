"""F-15 test 3 (plan 11.4.1): an authorization server that only supports DCR."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from tests.authz.f15_mock_as import MockAuthorizationServer, ensure_user, mcp_credential, start


@pytest.mark.asyncio
async def test_dcr_only_server_registers_a_client(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CIMD is configured on our side, but the server does not advertise it.
    monkeypatch.setattr(settings, "mcp_oauth_client_metadata_base_url", "https://moldy.example")
    await ensure_user(db)
    server = MockAuthorizationServer(registration=True, cimd=False)
    server.install(monkeypatch)
    cred = await mcp_credential(db)

    response, params = await start(client, cred)

    assert response.status_code == 200
    assert params["client_id"] == "dcr-client"
    assert len(server.registrations) == 1
    assert server.registrations[0]["redirect_uris"] == [
        "http://test/api/oauth2-credential/callback"
    ]
