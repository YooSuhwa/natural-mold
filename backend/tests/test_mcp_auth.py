from __future__ import annotations

import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.registry import registry
from app.mcp.auth import resolve_mcp_auth
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_mcp_auth_injects_http_bearer_header(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="http_bearer",
        name="Bearer",
        data={"token": "T-123"},
    )
    await db.commit()

    resolved = await resolve_mcp_auth(db, credential_id=cred.id, user_id=TEST_USER_ID)

    assert resolved.credentials == {"token": "T-123"}
    assert resolved.headers == {"Authorization": "Bearer T-123"}


@pytest.mark.asyncio
async def test_resolve_mcp_auth_refreshes_expired_oauth_and_persists(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": time.time() - 100,
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    await db.commit()

    async def fake_refresh(credentials: dict[str, Any]) -> dict[str, Any]:
        return {
            "access_token": "fresh",
            "refresh_token": credentials["refresh_token"],
            "expires_at": time.time() + 3600,
        }

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fake_refresh)

    resolved = await resolve_mcp_auth(db, credential_id=cred.id, user_id=TEST_USER_ID)

    assert resolved.headers == {"Authorization": "Bearer fresh"}
    await db.refresh(cred)
    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "fresh"
