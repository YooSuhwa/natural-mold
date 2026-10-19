"""F-15 test 4 (plan 11.4.1): pre-registration only, and CIMD when reachable.

Deviation from the plan order: a manually entered client_id wins over CIMD.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from tests.authz.f15_mock_as import MockAuthorizationServer, ensure_user, mcp_credential, start

CIMD_URL = "https://moldy.example/.well-known/oauth-client/default"


@pytest.mark.asyncio
async def test_preregistered_client_is_used_without_registration(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_user(db)
    server = MockAuthorizationServer(registration=False)
    server.install(monkeypatch)

    response, params = await start(client, await mcp_credential(db, client_id="pre-cid"))
    assert response.status_code == 200
    assert params["client_id"] == "pre-cid"
    assert server.registrations == []

    missing, _ = await start(client, await mcp_credential(db))
    assert missing.status_code == 400


@pytest.mark.asyncio
async def test_cimd_is_used_unless_a_manual_client_id_is_set(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/.well-known/oauth-client/default")).status_code == 404
    monkeypatch.setattr(settings, "mcp_oauth_client_metadata_base_url", "https://moldy.example")
    await ensure_user(db)
    server = MockAuthorizationServer(registration=True, cimd=True)
    server.install(monkeypatch)

    manual, manual_params = await start(client, await mcp_credential(db, client_id="pre-cid"))
    assert manual.status_code == 200
    assert manual_params["client_id"] == "pre-cid"

    dcr_cred = await mcp_credential(db, client_id="dcr-old", client_registration="dcr")
    _, dcr_params = await start(client, dcr_cred)
    assert dcr_params["client_id"] == CIMD_URL  # a client Moldy registered itself yields

    response, params = await start(client, await mcp_credential(db))

    assert response.status_code == 200
    assert params["client_id"] == CIMD_URL
    assert params["redirect_uri"] == "https://moldy.example/api/oauth2-credential/callback"
    assert server.registrations == []
    document = (await client.get("/.well-known/oauth-client/default")).json()
    assert document["client_id"] == CIMD_URL
    assert document["redirect_uris"] == [params["redirect_uri"]]
    assert document["token_endpoint_auth_method"] == "none"
    assert (await client.get("/.well-known/oauth-client/other")).status_code == 404
