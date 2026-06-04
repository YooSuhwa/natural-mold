"""Authenticated profile settings endpoints."""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import AsyncClient
from PIL import Image

from app.config import settings


async def _register(raw_client: AsyncClient, *, email: str = "profile@test.com") -> str:
    resp = await raw_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "correct horse",
            "display_name": "Initial",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["csrf_token"]


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (96, 80), color=(24, 144, 112)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_update_profile_changes_display_name_and_letter_avatar(raw_client: AsyncClient):
    csrf = await _register(raw_client)

    resp = await raw_client.patch(
        "/api/auth/me/profile",
        json={
            "display_name": "체스터",
            "avatar_mode": "initials",
            "avatar_initials": "체",
            "avatar_color": "sky",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert resp.status_code == 200, resp.text
    user = resp.json()
    assert user["display_name"] == "체스터"
    assert user["avatar_mode"] == "initials"
    assert user["avatar_initials"] == "체"
    assert user["avatar_color"] == "sky"
    assert user["avatar_image_url"] is None

    me = await raw_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "체스터"


@pytest.mark.asyncio
async def test_profile_update_allows_clearing_display_name(raw_client: AsyncClient):
    csrf = await _register(raw_client, email="clear-profile@test.com")

    resp = await raw_client.patch(
        "/api/auth/me/profile",
        json={"display_name": "", "avatar_mode": "auto", "avatar_initials": ""},
        headers={"X-CSRF-Token": csrf},
    )

    assert resp.status_code == 200, resp.text
    user = resp.json()
    assert user["display_name"] is None
    assert user["avatar_mode"] == "auto"
    assert user["avatar_initials"] is None


@pytest.mark.asyncio
async def test_avatar_image_upload_serve_and_delete(
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "user_avatar_dir", str(tmp_path / "users"))
    csrf = await _register(raw_client, email="avatar@test.com")

    upload = await raw_client.post(
        "/api/auth/me/avatar-image",
        files={"file": ("avatar.png", _png_bytes(), "image/png")},
        headers={"X-CSRF-Token": csrf},
    )

    assert upload.status_code == 200, upload.text
    user = upload.json()
    assert user["avatar_mode"] == "image"
    assert user["avatar_image_url"].startswith("/api/auth/me/avatar-image?t=")

    image = await raw_client.get("/api/auth/me/avatar-image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    rendered = Image.open(BytesIO(image.content))
    assert rendered.format == "WEBP"
    assert max(rendered.size) == 512

    deleted = await raw_client.delete(
        "/api/auth/me/avatar-image",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["avatar_mode"] == "initials"
    assert deleted.json()["avatar_image_url"] is None

    missing = await raw_client.get("/api/auth/me/avatar-image")
    assert missing.status_code == 204


@pytest.mark.asyncio
async def test_avatar_image_rejects_non_image(raw_client: AsyncClient):
    csrf = await _register(raw_client, email="bad-avatar@test.com")

    resp = await raw_client.post(
        "/api/auth/me/avatar-image",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers={"X-CSRF-Token": csrf},
    )

    assert resp.status_code == 422
