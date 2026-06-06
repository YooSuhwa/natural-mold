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
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.auth.jwt import hash_refresh_token
from app.config import settings
from app.models.refresh_token import RefreshToken
from tests.conftest import TestSession


async def _register_and_login(
    client: AsyncClient,
    email: str = "rt@test.com",
    user_agent: str | None = None,
) -> str:
    """Register a fresh user. Returns the issued refresh JWT."""

    settings.allow_first_user_as_admin = False
    headers = {"user-agent": user_agent} if user_agent else None
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "name": "RT User"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    refresh = resp.cookies[settings.cookie_name_refresh]
    assert refresh
    return refresh


async def _post_refresh(
    client: AsyncClient,
    refresh_token: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    client.cookies.set(settings.cookie_name_refresh, refresh_token)
    return await client.post("/api/auth/refresh", headers=headers)


async def _register_and_first_rotate(
    client: AsyncClient, user_agent: str
) -> tuple[str, str, dict[str, str]]:
    """Register a user, perform Tab A's winning rotation, return the
    stale original cookie + rotated cookie + ``user-agent`` headers.

    Centralises the boilerplate every race scenario needs to reach the
    interesting branch (a revoked-but-replaced row to re-present).
    """

    headers = {"user-agent": user_agent}
    rt = await _register_and_login(client, user_agent=user_agent)
    first = await _post_refresh(client, rt, headers=headers)
    assert first.status_code == 200, first.text
    rt_a = first.cookies[settings.cookie_name_refresh]
    return rt, rt_a, headers


@pytest.mark.asyncio
async def test_refresh_rotates_and_revokes_previous(raw_client: AsyncClient):
    rt = await _register_and_login(raw_client)

    resp = await _post_refresh(raw_client, rt)
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
    """Replay detection: re-using a rotated refresh from a different UA → 401.

    The UA mismatch is what classifies the second presentation as an
    attack rather than a same-browser tab race. Without it, the request
    falls into the grace-window chain path (covered separately).
    """

    rt = await _register_and_login(raw_client)

    # First rotation succeeds (legit browser).
    first = await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "LegitBrowser/1.0"},
    )
    assert first.status_code == 200

    # Replay the original from a different UA — must 401.
    replay = await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "AttackerBrowser/9.9"},
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_replay_revokes_all_active(raw_client: AsyncClient):
    rt = await _register_and_login(raw_client)
    first = await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "LegitBrowser/1.0"},
    )
    new_rt = first.cookies[settings.cookie_name_refresh]
    await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "AttackerBrowser/9.9"},
    )

    async with TestSession() as db:
        rows = (await db.execute(select(RefreshToken))).scalars().all()
        assert rows
        assert all(r.revoked_at is not None for r in rows)

    follow_up = await _post_refresh(
        raw_client,
        new_rt,
        headers={"user-agent": "LegitBrowser/1.0"},
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

    resp = await _post_refresh(raw_client, rt)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_401(raw_client: AsyncClient):
    resp = await raw_client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_race_within_grace_window_chains_instead_of_replay(
    raw_client: AsyncClient,
):
    """Two-tab race: stale token re-presented within grace + UA match → success.

    Reproduces the production bug from 2026-05-18 where two tabs hitting
    /api/auth/refresh simultaneously caused the loser to be classified
    as a replay and the entire user whitelist to be revoked. The grace
    path issues fresh tokens off the chain head and leaves prior
    rotations untouched.
    """

    rt, rt_a, headers = await _register_and_first_rotate(
        raw_client, user_agent="Mozilla/5.0 (TabRaceTest)"
    )

    # Tab B loses — same original cookie, same UA, well inside grace.
    second = await _post_refresh(raw_client, rt, headers=headers)
    assert second.status_code == 200, second.text
    rt_b = second.cookies[settings.cookie_name_refresh]
    assert rt_b not in {rt, rt_a}

    async with TestSession() as db:
        rows = (await db.execute(select(RefreshToken))).scalars().all()
        by_hash = {r.token_hash: r for r in rows}
        assert by_hash[hash_refresh_token(rt)].revoked_at is not None
        assert by_hash[hash_refresh_token(rt_a)].revoked_at is not None
        head = by_hash[hash_refresh_token(rt_b)]
        assert head.revoked_at is None
        assert by_hash[hash_refresh_token(rt)].replaced_by_id == by_hash[
            hash_refresh_token(rt_a)
        ].id
        assert by_hash[hash_refresh_token(rt_a)].replaced_by_id == head.id


@pytest.mark.asyncio
async def test_refresh_race_with_user_agent_mismatch_is_replay(
    raw_client: AsyncClient,
):
    """UA mismatch breaks the race-vs-replay heuristic → mass-revoke."""

    rt = await _register_and_login(raw_client)

    first = await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "BrowserA/1.0"},
    )
    assert first.status_code == 200

    # Same stale cookie from a different UA — treat as theft.
    replay = await _post_refresh(
        raw_client,
        rt,
        headers={"user-agent": "BrowserB/2.0"},
    )
    assert replay.status_code == 401

    async with TestSession() as db:
        rows = (await db.execute(select(RefreshToken))).scalars().all()
        assert rows
        assert all(r.revoked_at is not None for r in rows)


@pytest.mark.asyncio
async def test_refresh_race_outside_grace_window_is_replay(raw_client: AsyncClient):
    """Same-cookie re-use *after* the grace window expired → mass-revoke."""

    rt, _rt_a, headers = await _register_and_first_rotate(
        raw_client, user_agent="Mozilla/5.0 (GraceWindowTest)"
    )

    # Backdate the original row's revocation so it's outside grace.
    async with TestSession() as db:
        old = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(rt)
                )
            )
        ).scalar_one()
        old.revoked_at = (
            datetime.now(UTC) - timedelta(
                seconds=settings.refresh_rotation_grace_seconds + 30
            )
        ).replace(tzinfo=None)
        await db.commit()

    replay = await _post_refresh(raw_client, rt, headers=headers)
    assert replay.status_code == 401

    async with TestSession() as db:
        rows = (await db.execute(select(RefreshToken))).scalars().all()
        assert all(r.revoked_at is not None for r in rows)


@pytest.mark.asyncio
async def test_refresh_replay_after_replacement_revoked_is_replay(
    raw_client: AsyncClient,
):
    """Replacement no longer active → don't chain; fall through to replay."""

    rt, rt_a, headers = await _register_and_first_rotate(
        raw_client, user_agent="Mozilla/5.0 (RevokedHeadTest)"
    )

    # Logout the replacement leg — chain head is now revoked.
    async with TestSession() as db:
        head = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(rt_a)
                )
            )
        ).scalar_one()
        head.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()

    replay = await _post_refresh(raw_client, rt, headers=headers)
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_refresh_chain_walk_aborts_at_depth_limit(
    raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
):
    """Pathological loop: ``_find_race_chain_head`` repeatedly returns a row
    that ``_lock_row`` then sees as revoked — without the depth bound the
    chain-walk could spin forever. We stub the chain head to always point
    back at the original row to simulate a degenerate cycle and assert
    the bound bites at ``_MAX_CHAIN_FOLLOW``.
    """

    from app.services import auth_service

    rt, rt_a, headers = await _register_and_first_rotate(
        raw_client, user_agent="Mozilla/5.0 (ChainBoundTest)"
    )

    async with TestSession() as db:
        original = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(rt)
                )
            )
        ).scalar_one()
        original_id = original.id

    # Force every chain-walk hop to land back on the (revoked) original
    # row so the loop never finds an active candidate.
    async def _always_return_original(db, row, request, now):
        return await db.get(RefreshToken, original_id)

    monkeypatch.setattr(
        auth_service, "_find_race_chain_head", _always_return_original
    )

    response = await _post_refresh(raw_client, rt, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh"


@pytest.mark.asyncio
async def test_refresh_unknown_token_returns_401(raw_client: AsyncClient):
    """A well-formed JWT with no matching DB row → invalid_refresh.

    Mints a refresh token signed by the same secret but never persisted —
    simulates either forgery or an aggressively-pruned whitelist.
    """

    from app.auth.jwt import create_refresh_token

    bogus, _, _ = create_refresh_token(uuid.uuid4())
    resp = await _post_refresh(raw_client, bogus)
    assert resp.status_code == 401
