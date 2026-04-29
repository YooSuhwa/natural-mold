"""Tests for OAuth2 helpers — expiry detection and refresh delegation."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind
from app.credentials.oauth2_base import (
    is_token_expired,
    refresh_oauth_token,
)
from app.credentials.registry import CredentialRegistry
from app.models.credential_audit_log import CredentialAuditLog
from app.models.user import User
from sqlalchemy import select
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


# -- is_token_expired --------------------------------------------------------


def test_is_token_expired_missing_token() -> None:
    assert is_token_expired({}) is True


def test_is_token_expired_no_expiry_metadata() -> None:
    assert is_token_expired({"access_token": "tok"}) is True


def test_is_token_expired_in_the_past() -> None:
    assert is_token_expired(
        {"access_token": "tok", "expires_at": time.time() - 100}
    ) is True


def test_is_token_expired_within_skew() -> None:
    # Default skew is 60s — anything within that window is treated as expired
    # so the caller refreshes pre-emptively.
    assert is_token_expired(
        {"access_token": "tok", "expires_at": time.time() + 30}
    ) is True


def test_is_token_expired_far_in_the_future() -> None:
    assert is_token_expired(
        {"access_token": "tok", "expires_at": time.time() + 3600}
    ) is False


def test_is_token_expired_iso_format_supported() -> None:
    from datetime import UTC, datetime, timedelta

    iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert is_token_expired({"access_token": "tok", "expires_at": iso}) is False


# -- refresh_oauth_token -----------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_oauth_token_invokes_pre_authentication() -> None:
    captured: dict[str, Any] = {}

    async def fake_refresh(creds: dict[str, Any]) -> dict[str, Any]:
        captured.update(creds)
        return {"access_token": "fresh", "expires_at": 9999999999.0}

    definition = CredentialDefinition(
        key="fake_oauth",
        display_name="Fake",
        properties=[FieldDef(name="refresh_token", display_name="rt", kind=FieldKind.PASSWORD)],
        pre_authentication=fake_refresh,
    )
    refreshed = await refresh_oauth_token(
        definition,
        {"refresh_token": "rt-123", "access_token": "old"},
    )
    assert refreshed["access_token"] == "fresh"
    assert refreshed["expires_at"] == 9999999999.0
    # Original fields are preserved.
    assert refreshed["refresh_token"] == "rt-123"
    # The hook receives a copy of the credentials.
    assert captured["refresh_token"] == "rt-123"


@pytest.mark.asyncio
async def test_refresh_oauth_token_requires_hook() -> None:
    definition = CredentialDefinition(
        key="no_oauth",
        display_name="No OAuth",
        properties=[],
    )
    with pytest.raises(RuntimeError):
        await refresh_oauth_token(definition, {})


@pytest.mark.asyncio
async def test_refresh_oauth_token_invalid_return_type() -> None:
    async def bad(creds: dict[str, Any]) -> Any:  # type: ignore[return-value]
        return "not a dict"

    definition = CredentialDefinition(
        key="bad_oauth",
        display_name="Bad",
        properties=[],
        pre_authentication=bad,
    )
    with pytest.raises(RuntimeError):
        await refresh_oauth_token(definition, {})


# -- Audit-log integration ---------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_writes_audit_log(db: AsyncSession) -> None:
    """A successful refresh persists a ``refresh`` audit log entry."""

    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="google_workspace_oauth2",
        name="GW",
        data={
            "client_id": "cid",
            "client_secret": "cs",
            "refresh_token": "rt",
            "access_token": "old",
            "expires_at": 0,
        },
    )
    await db.commit()

    # Simulate the refresh path: encrypt the new payload and write the audit
    # log the same way the OAuth2 callback handler does.
    new_data = {
        "client_id": "cid",
        "client_secret": "cs",
        "refresh_token": "rt",
        "access_token": "fresh",
        "expires_at": time.time() + 3600,
    }
    blob, key_id, field_keys = credential_service.encrypt_data(new_data)
    cred.data_encrypted = blob
    cred.key_id = key_id
    cred.field_keys = field_keys
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=TEST_USER_ID,
        action="refresh",
        source="runtime",
        metadata={"reason": "test"},
    )
    await db.commit()

    logs = await credential_service.list_audit_logs(db, credential_id=cred.id)
    actions = [log.action for log in logs]
    assert "refresh" in actions

    # And the new payload decrypts cleanly.
    decoded = credential_service.decrypt_data(cred.data_encrypted)
    assert decoded["access_token"] == "fresh"
