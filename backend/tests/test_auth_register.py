"""POST /api/auth/register — first-user promotion + validation.

ADR-016 §5.1. Verifies the registration flow end-to-end against the real
app (no auth/CSRF overrides) so we exercise the cookie issuance path.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings


def _payload(email: str = "u@test.com") -> dict[str, str]:
    return {"email": email, "password": "correct horse", "display_name": "Tester"}


@pytest.mark.asyncio
async def test_first_user_auto_promoted_to_super_user(raw_client: AsyncClient):
    """Default ``allow_first_user_as_admin=True`` → first signup is super_user."""

    saved = settings.allow_first_user_as_admin
    settings.allow_first_user_as_admin = True
    try:
        resp = await raw_client.post("/api/auth/register", json=_payload())
    finally:
        settings.allow_first_user_as_admin = saved

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["is_super_user"] is True
    assert body["user"]["display_name"] == "Tester"
    assert body["user"]["avatar_mode"] == "auto"
    assert body["user"]["avatar_color"] == "mint"
    assert body["user"]["avatar_image_url"] is None
    assert body["csrf_token"]
    cookies = resp.cookies
    assert settings.cookie_name_access in cookies
    assert settings.cookie_name_refresh in cookies
    assert settings.cookie_name_csrf in cookies


@pytest.mark.asyncio
async def test_first_user_not_promoted_when_toggle_disabled(raw_client: AsyncClient):
    """``ALLOW_FIRST_USER_AS_ADMIN=false`` → even the first user is non-super."""

    saved = settings.allow_first_user_as_admin
    settings.allow_first_user_as_admin = False
    try:
        resp = await raw_client.post("/api/auth/register", json=_payload())
    finally:
        settings.allow_first_user_as_admin = saved

    assert resp.status_code == 201
    assert resp.json()["user"]["is_super_user"] is False


@pytest.mark.asyncio
async def test_second_user_never_super(raw_client: AsyncClient):
    """Subsequent signups are always plain users regardless of toggle."""

    settings.allow_first_user_as_admin = True
    first = await raw_client.post("/api/auth/register", json=_payload("a@test.com"))
    assert first.status_code == 201
    assert first.json()["user"]["is_super_user"] is True

    second = await raw_client.post("/api/auth/register", json=_payload("b@test.com"))
    assert second.status_code == 201
    assert second.json()["user"]["is_super_user"] is False


@pytest.mark.asyncio
async def test_duplicate_email_returns_409(raw_client: AsyncClient):
    first = await raw_client.post("/api/auth/register", json=_payload("dup@test.com"))
    assert first.status_code == 201

    dup = await raw_client.post("/api/auth/register", json=_payload("dup@test.com"))
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "email_already_exists"


@pytest.mark.asyncio
async def test_password_too_short_returns_422(raw_client: AsyncClient):
    bad = {**_payload("short@test.com"), "password": "short"}
    resp = await raw_client.post("/api/auth/register", json=bad)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_name_returns_422(raw_client: AsyncClient):
    bad = {"email": "noname@test.com", "password": "correct horse"}
    resp = await raw_client.post("/api/auth/register", json=bad)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_legacy_name_payload_initializes_display_name(raw_client: AsyncClient):
    resp = await raw_client.post(
        "/api/auth/register",
        json={"email": "legacy@test.com", "password": "correct horse", "name": "Legacy Name"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["name"] == "Legacy Name"
    assert body["user"]["display_name"] == "Legacy Name"


@pytest.mark.asyncio
async def test_invalid_email_returns_422(raw_client: AsyncClient):
    bad = {**_payload(), "email": "not-an-email"}
    resp = await raw_client.post("/api/auth/register", json=bad)
    assert resp.status_code == 422
