"""Credential key rotation cron — re-encrypts rows under stale keys.

The job is registered by the lifespan; this test exercises the underlying
async helper directly so we don't need a running scheduler.
"""

from __future__ import annotations

import asyncio
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
@pytest.mark.usefixtures("_user", "_restore_keys")
async def test_rotation_re_encrypts_all_stale_rows() -> None:
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
            (
                await db.execute(
                    select(CredentialAuditLog).where(CredentialAuditLog.action == "rotate")
                )
            )
            .scalars()
            .all()
        )
        assert len(log_rows) == len(created)
        for log in log_rows:
            assert log.log_metadata == {"key_id": active_b_id}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore_keys")
async def test_rotation_is_noop_when_already_active() -> None:
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
            (
                await db.execute(
                    select(CredentialAuditLog).where(CredentialAuditLog.action == "rotate")
                )
            )
            .scalars()
            .all()
        )
        assert log_rows == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore_keys")
async def test_rotation_wrapper_wires_patched_batch_size_through() -> None:
    """The scheduler wrapper must inject the (patched) ``_ROTATION_BATCH``
    into the rotation loop — not a hardcoded batch of its own.

    The loop opens one session (and runs one SELECT) per page, so 5 stale
    rows at batch=2 must page 2+2+1 → 3 session-factory calls. A wrapper
    that hardcoded e.g. ``batch_size=100`` would still rotate all 5 rows in
    a single page, which is exactly the mutation this pins. It also makes
    the ``notin_`` regression test below honest: that test silently relies
    on ``_ROTATION_BATCH=2`` actually reaching the loop.
    """

    key_a = secrets.token_hex(32)
    key_b = secrets.token_hex(32)
    _swap_keys(key_a)

    created: list[uuid.UUID] = []
    async with TestSession() as db:
        for idx in range(5):
            cred = await credential_service.create(
                db,
                user_id=TEST_USER_ID,
                definition_key="openai",
                name=f"batch-{idx}",
                data={"api_key": f"sk-batch-{idx}"},
            )
            created.append(cred.id)
        await db.commit()

    _swap_keys(key_b, key_a)
    active_b_id = key_provider.get_active_key_id()

    session_calls = 0

    def _counting_session() -> AsyncSession:
        nonlocal session_calls
        session_calls += 1
        return TestSession()

    with (
        patch("app.scheduler.async_session", _counting_session),
        patch("app.scheduler._ROTATION_BATCH", 2),
    ):
        rotated = await rotate_credentials_to_active_key()

    # Everything rotated…
    assert rotated == len(created)
    async with TestSession() as db:
        rows = (await db.execute(select(Credential).where(Credential.id.in_(created)))).scalars()
        assert all(row.key_id == active_b_id for row in rows)

    # …AND batch paging actually happened: 5 rows at batch=2 need at least
    # 3 pages (2+2+1). A hardcoded large batch finishes in a single session.
    assert session_calls >= 3, (
        f"expected >=3 session pages for 5 rows at batch=2, got {session_calls} — "
        "the wrapper is not forwarding _ROTATION_BATCH"
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore_keys")
async def test_rotation_terminates_when_rows_keep_failing() -> None:
    """A full batch of persistently failing rows must not spin the loop
    forever: failed ids are excluded from the next fetch, so the job
    finishes (rotated=0) and leaves the rows for the next scheduled run.

    Batch is patched to 2 with 3 failing rows so the pre-fix code path
    (re-fetching the same full batch endlessly) would hang; wait_for turns
    a regression into a test failure instead of a hung suite.
    """

    key_a = secrets.token_hex(32)
    key_b = secrets.token_hex(32)
    _swap_keys(key_a)

    async with TestSession() as db:
        for idx in range(3):
            await credential_service.create(
                db,
                user_id=TEST_USER_ID,
                definition_key="openai",
                name=f"fail-{idx}",
                data={"api_key": f"sk-{idx}"},
            )
        await db.commit()

    _swap_keys(key_b, key_a)

    async def _always_fail(db: AsyncSession, cred: Credential) -> None:
        raise RuntimeError("decrypt failed")

    with (
        patch("app.scheduler.async_session", TestSession),
        patch("app.scheduler._ROTATION_BATCH", 2),
        patch.object(credential_service, "re_encrypt_with_active_key", _always_fail),
    ):
        rotated = await asyncio.wait_for(rotate_credentials_to_active_key(), timeout=10)

    assert rotated == 0

    # Rows stay on the stale key — untouched, retried next run.
    async with TestSession() as db:
        rows = (await db.execute(select(Credential))).scalars().all()
        active_id = key_provider.get_active_key_id()
        assert all(row.key_id != active_id for row in rows)
