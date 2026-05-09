"""POST /api/auth/refresh — rotation, replay detection, expiry.

ADR-016 §5.2 — refresh rotation is the single most security-sensitive
flow. Replay (presenting an already-revoked refresh) MUST burn the entire
user's active token set so a stolen leg can't be re-used after the
victim's browser refreshed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.jwt import hash_refresh_token
from app.config import settings
from app.models.refresh_token import RefreshToken
from tests.conftest import TestSession


async def _register_and_login(client: AsyncClient, email: str = "rt@test.com") -> str:
    """Register a fresh user. Returns the issued refresh JWT."""

    settings.allow_first_user_as_admin = False
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "name": "RT User"},
    )
    assert resp.status_code == 201, resp.text
    refresh = resp.cookies[settings.cookie_name_refresh]
    assert refresh
    return refresh


@pytest.mark.asyncio
async def test_refresh_rotates_and_revokes_previous(raw_client: AsyncClient):
    rt = await _register_and_login(raw_client)

    resp = await raw_client.post(
        "/api/auth/refresh",
        cookies={settings.cookie_name_refresh: rt},
    )
    assert resp.status_code == 200, resp.text
    new_rt = resp.cookies[settings.cookie_name_refresh]
    assert new_rt
    assert new_rt != rt

    # The old refresh row is now revoked.
    async with TestSession() as db:
        old = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(rt)
                )
            )
        ).scalar_one_or_none()
        assert old is not None
        assert old.revoked_at is not None

        # The new refresh row is active.
        new_row = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(new_rt)
                )
            )
        ).scalar_one_or_none()
        assert new_row is not None
        assert new_row.revoked_at is None


@pytest.mark.asyncio
async def test_refresh_replay_logs_warning_and_returns_401(raw_client: AsyncClient):
    """Replay detection: re-using a rotated refresh returns 401."""

    rt = await _register_and_login(raw_client)

    # First rotation succeeds.
    first = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: rt}
    )
    assert first.status_code == 200

    # Replay the original — must 401.
    replay = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: rt}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_replay_revokes_all_active(raw_client: AsyncClient):
    rt = await _register_and_login(raw_client)
    first = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: rt}
    )
    new_rt = first.cookies[settings.cookie_name_refresh]
    await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: rt}
    )

    async with TestSession() as db:
        rows = (await db.execute(select(RefreshToken))).scalars().all()
        assert rows
        assert all(r.revoked_at is not None for r in rows)

    follow_up = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: new_rt}
    )
    assert follow_up.status_code == 401


@pytest.mark.asyncio
async def test_refresh_expired_returns_401(raw_client: AsyncClient):
    rt = await _register_and_login(raw_client)

    # Force the row's ``expires_at`` into the past.
    async with TestSession() as db:
        row = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(rt)
                )
            )
        ).scalar_one()
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        await db.commit()

    resp = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: rt}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_401(raw_client: AsyncClient):
    resp = await raw_client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_unknown_token_returns_401(raw_client: AsyncClient):
    """A well-formed JWT with no matching DB row → invalid_refresh.

    Mints a refresh token signed by the same secret but never persisted —
    simulates either forgery or an aggressively-pruned whitelist.
    """

    from app.auth.jwt import create_refresh_token

    bogus, _, _ = create_refresh_token(uuid.uuid4())
    resp = await raw_client.post(
        "/api/auth/refresh", cookies={settings.cookie_name_refresh: bogus}
    )
    assert resp.status_code == 401
