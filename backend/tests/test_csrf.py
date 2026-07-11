"""CSRF double-submit verification.

ADR-016 §5.3 — every mutating request to an authenticated route MUST
carry the ``X-CSRF-Token`` header equal to the ``moldy_csrf`` cookie, and
the embedded JWT ``sub`` MUST match the current user. ``GET``/``HEAD``/
``OPTIONS`` are exempt; the auth bootstrap endpoints are exempt (no user
yet to issue a token).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_csrf_token
from app.config import settings
from tests.conftest import register_session


async def _login(client: AsyncClient, email: str = "csrf@test.com") -> dict[str, str]:
    """Register + login. Returns ``{"csrf": ..., "rt": ..., "at": ...}`` cookies."""

    sess = await register_session(client, email=email, name="CSRF")
    return {
        "csrf": sess.csrf,
        "csrf_cookie": sess.cookies[settings.cookie_name_csrf],
        "at": sess.cookies[settings.cookie_name_access],
        "rt": sess.cookies[settings.cookie_name_refresh],
    }


@pytest.mark.asyncio
async def test_get_request_passes_without_csrf(raw_client: AsyncClient):
    """``GET /api/auth/me`` must succeed with cookies but no X-CSRF-Token."""

    tokens = await _login(raw_client)
    raw_client.cookies.set(settings.cookie_name_access, tokens["at"])
    resp = await raw_client.get("/api/auth/me")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_mutation_without_csrf_header_returns_403(raw_client: AsyncClient):
    """Mutation with auth cookie but no X-CSRF-Token header → 403."""

    await _login(raw_client)
    # cookies are already on the client from register.
    resp = await raw_client.post("/api/auth/logout")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_mismatch"


@pytest.mark.asyncio
async def test_mutation_with_mismatched_header_returns_403(raw_client: AsyncClient):
    """Header != cookie → 403 even when both are well-formed."""

    await _login(raw_client)
    # Forge a different (valid-format) JWT for X-CSRF-Token.
    import uuid

    other_token = create_csrf_token(uuid.uuid4())
    resp = await raw_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": other_token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mutation_with_wrong_subject_csrf_returns_403(raw_client: AsyncClient):
    """X-CSRF-Token signed for a different user → 403 (cross-account replay).

    Set both header *and* cookie to the same forged token so the
    double-submit equality check passes; the ``sub`` mismatch must still
    reject.
    """

    await _login(raw_client)
    import uuid

    other_token = create_csrf_token(uuid.uuid4())
    raw_client.cookies.set(settings.cookie_name_csrf, other_token)
    resp = await raw_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": other_token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mutation_with_garbage_csrf_returns_403(raw_client: AsyncClient):
    """Header with malformed token → 403 (decode_token raises InvalidTokenError)."""

    await _login(raw_client)
    raw_client.cookies.set(settings.cookie_name_csrf, "not-a-jwt")
    resp = await raw_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": "not-a-jwt"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mutation_with_correct_csrf_passes(raw_client: AsyncClient):
    """Header == cookie + ``sub`` matches user → 200."""

    tokens = await _login(raw_client)
    resp = await raw_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": tokens["csrf"]},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_register_login_refresh_exempt_from_csrf(raw_client: AsyncClient):
    """Bootstrap endpoints have no caller user yet — CSRF would be impossible."""

    settings.allow_first_user_as_admin = False
    reg = await raw_client.post(
        "/api/auth/register",
        json={"email": "noc@test.com", "password": "correct horse", "name": "Noc"},
    )
    assert reg.status_code == 201
    raw_client.cookies.clear()

    login = await raw_client.post(
        "/api/auth/login",
        json={"email": "noc@test.com", "password": "correct horse"},
    )
    assert login.status_code == 200

    # /refresh: cookies-only, no X-CSRF-Token header.
    refresh = await raw_client.post("/api/auth/refresh")
    assert refresh.status_code == 200
