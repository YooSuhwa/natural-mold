from __future__ import annotations

import importlib

import pytest

mcp_oauth2 = importlib.import_module("app.credentials.definitions.mcp_oauth2")


@pytest.mark.asyncio
async def test_mcp_oauth2_exchanges_authorization_code(monkeypatch) -> None:
    async def fake_exchange_authorization_code(**kwargs):
        assert kwargs["code"] == "auth-code"
        assert kwargs["code_verifier"] == "verifier"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(
        mcp_oauth2,
        "exchange_authorization_code",
        fake_exchange_authorization_code,
    )

    assert mcp_oauth2.definition.pre_authentication is not None
    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "authorization_code": "auth-code",
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "client_secret": "",
            "authentication": "none",
            "redirect_uri": "http://localhost/callback",
            "code_verifier": "verifier",
            "refresh_token": "old-refresh",
        }
    )

    assert patch["access_token"] == "fresh"
    assert patch["refresh_token"] == "refresh"


@pytest.mark.asyncio
async def test_mcp_oauth2_refresh_preserves_new_token(monkeypatch) -> None:
    async def fake_refresh_access_token(**kwargs):
        assert kwargs["refresh_token"] == "refresh"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(mcp_oauth2, "refresh_access_token", fake_refresh_access_token)

    assert mcp_oauth2.definition.pre_authentication is not None
    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "client_secret": "",
            "authentication": "none",
            "refresh_token": "refresh",
        }
    )

    assert patch["access_token"] == "fresh"


@pytest.mark.asyncio
async def test_mcp_oauth2_accepts_token_url_alias(monkeypatch) -> None:
    async def fake_refresh_access_token(**kwargs):
        assert kwargs["token_endpoint"] == "https://issuer.example/token"
        return {"access_token": "fresh", "expires_at": 9999999999.0}

    monkeypatch.setattr(mcp_oauth2, "refresh_access_token", fake_refresh_access_token)

    assert mcp_oauth2.definition.pre_authentication is not None
    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "token_url": "https://issuer.example/token",
            "client_id": "cid",
            "refresh_token": "refresh",
        }
    )

    assert patch["access_token"] == "fresh"


@pytest.mark.asyncio
async def test_mcp_oauth2_legacy_client_credentials_default_to_body_auth(monkeypatch) -> None:
    async def fake_fetch_client_credentials_token(**kwargs):
        assert kwargs["token_endpoint"] == "https://issuer.example/token"
        assert kwargs["client_id"] == "cid"
        assert kwargs["client_secret"] == "secret"
        assert kwargs["authentication"] == "body"
        return {"access_token": "fresh", "expires_at": 9999999999.0}

    monkeypatch.setattr(
        mcp_oauth2,
        "fetch_client_credentials_token",
        fake_fetch_client_credentials_token,
    )

    assert mcp_oauth2.definition.pre_authentication is not None
    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "token_url": "https://issuer.example/token",
            "client_id": "cid",
            "client_secret": "secret",
        }
    )

    assert patch["access_token"] == "fresh"
