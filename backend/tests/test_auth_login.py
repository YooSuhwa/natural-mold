"""POST /api/auth/login — credential verification, lockout, attempt counter.

ADR-016 §5.1 / user_service.MAX_FAILED_ATTEMPTS=5.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models.user import User
from tests.conftest import TestSession


async def _fresh_user(email: str) -> User:
    """Re-read the User row in a brand-new session (avoids identity-map staleness)."""

    async with TestSession() as fresh:
        return (
            await fresh.execute(select(User).where(User.email == email))
        ).scalar_one()


async def _register(
    client: AsyncClient,
    *,
    email: str = "login@test.com",
    password: str = "correct horse",
) -> None:
    saved = settings.allow_first_user_as_admin
    settings.allow_first_user_as_admin = False
    try:
        resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "name": "Login User"},
        )
    finally:
        settings.allow_first_user_as_admin = saved
    assert resp.status_code == 201
    # Drop the cookies the register set so login is the next clean request.
    client.cookies.clear()


@pytest.mark.asyncio
async def test_login_success_sets_cookies_and_csrf(raw_client: AsyncClient):
    await _register(raw_client)

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "login@test.com", "password": "correct horse"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["csrf_token"]
    assert body["user"]["email"] == "login@test.com"
    cookies = resp.cookies
    assert settings.cookie_name_access in cookies
    assert settings.cookie_name_refresh in cookies
    assert settings.cookie_name_csrf in cookies


@pytest.mark.asyncio
async def test_wrong_password_returns_401_and_increments_counter(
    raw_client: AsyncClient,
):
    await _register(raw_client)

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "login@test.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401

    user = await _fresh_user("login@test.com")
    assert user.failed_login_attempts == 1


@pytest.mark.asyncio
async def test_five_failures_lock_account_returns_423(raw_client: AsyncClient):
    await _register(raw_client)

    for _ in range(5):
        await raw_client.post(
            "/api/auth/login",
            json={"email": "login@test.com", "password": "wrong"},
        )

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "login@test.com", "password": "correct horse"},
    )
    assert resp.status_code == 423
    user = await _fresh_user("login@test.com")
    assert user.locked_until is not None


@pytest.mark.asyncio
async def test_successful_login_clears_failed_state(raw_client: AsyncClient):
    """Successful login records ``last_login_at`` + zeros the counter."""

    await _register(raw_client)

    ok = await raw_client.post(
        "/api/auth/login",
        json={"email": "login@test.com", "password": "correct horse"},
    )
    assert ok.status_code == 200

    user = await _fresh_user("login@test.com")
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert user.last_login_at is not None


@pytest.mark.asyncio
async def test_unknown_email_returns_401_uniform_message(raw_client: AsyncClient):
    """Enumeration oracle prevention — same status/code for unknown email."""

    resp = await raw_client.post(
        "/api/auth/login",
        json={"email": "ghost@test.com", "password": "anything"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
