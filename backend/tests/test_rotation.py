"""Credential key rotation cron — re-encrypts rows under stale keys.

The job is registered by the lifespan; this test exercises the underlying
async helper directly so we don't need a running scheduler.
"""

from __future__ import annotations

import secrets
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.credential_audit_log import CredentialAuditLog
from app.models.user import User
from app.scheduler import rotate_credentials_to_active_key
from app.security import key_provider
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture
async def _user(db: AsyncSession) -> None:
    db.add(User(id=TEST_USER_ID, email="rot@test", name="rot"))
    await db.commit()


def _swap_keys(*hex_keys: str) -> None:
    """Swap the active encryption keys (first arg = active)."""

    settings.encryption_keys = ",".join(hex_keys)
    key_provider.reset_cache()


@pytest.fixture
def _restore_keys():
    original = settings.encryption_keys
    yield
    settings.encryption_keys = original
    key_provider.reset_cache()


@pytest.mark.asyncio
async def test_rotation_re_encrypts_all_stale_rows(
    _user: None, _restore_keys: None
) -> None:
    """Encrypt N rows with key A, swap active to key B, run rotation, expect
    every row's ``key_id`` to match B and an audit log per row."""

    key_a = secrets.token_hex(32)
    key_b = secrets.token_hex(32)

    # Encrypt with key_a active.
    _swap_keys(key_a)
    active_a_id = key_provider.get_active_key_id()

    created: list[uuid.UUID] = []
    async with TestSession() as db:
        for idx in range(3):
            cred = await credential_service.create(
                db,
                user_id=TEST_USER_ID,
                definition_key="openai",
                name=f"rot-{idx}",
                data={"api_key": f"sk-{idx}"},
            )
            created.append(cred.id)
        await db.commit()

    # Promote key_b to active. Both keys remain so existing blobs decrypt.
    _swap_keys(key_b, key_a)
    active_b_id = key_provider.get_active_key_id()
    assert active_b_id != active_a_id

    # Rotation needs to write to the same SQLite engine the test session uses.
    with patch("app.scheduler.async_session", TestSession):
        rotated = await rotate_credentials_to_active_key()

    assert rotated == len(created)

    async with TestSession() as db:
        rows = (await db.execute(select(Credential).where(Credential.id.in_(created)))).scalars()
        for row in rows:
            assert row.key_id == active_b_id

        log_rows = (
            await db.execute(
                select(CredentialAuditLog).where(CredentialAuditLog.action == "rotate")
            )
        ).scalars().all()
        assert len(log_rows) == len(created)
        for log in log_rows:
            assert log.log_metadata == {"key_id": active_b_id}


@pytest.mark.asyncio
async def test_rotation_is_noop_when_already_active(
    _user: None, _restore_keys: None
) -> None:
    """Re-running rotation when every row is already on the active key yields
    zero work and writes no audit log."""

    key_only = secrets.token_hex(32)
    _swap_keys(key_only)

    async with TestSession() as db:
        await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="stable",
            data={"api_key": "sk-stable"},
        )
        await db.commit()

    with patch("app.scheduler.async_session", TestSession):
        rotated = await rotate_credentials_to_active_key()

    assert rotated == 0

    async with TestSession() as db:
        log_rows = (
            await db.execute(
                select(CredentialAuditLog).where(CredentialAuditLog.action == "rotate")
            )
        ).scalars().all()
        assert log_rows == []
