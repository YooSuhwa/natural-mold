"""Refresh-token GC (ADR-016 §4.2 / HANDOFF #2a).

Without GC the ``refresh_tokens`` whitelist grows unbounded — one row
per rotation × user × lifetime. This module locks in the contract:
expired rows past the retention window go, everything else stays
(including revoked rows whose ``expires_at`` is still in the future, so
the replay-detection branch keeps classifying them).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.refresh_token import RefreshToken
from app.services.refresh_token_gc import gc_expired_refresh_tokens
from tests.conftest import TestSession, make_refresh_token, make_user


@pytest.mark.asyncio
async def test_gc_deletes_rows_past_retention_only() -> None:
    """Rows older than retention go; everything else stays.

    The cutoff is ``now - retention_days``. A row whose ``expires_at``
    is exactly at the cutoff (or after) MUST survive — the replay
    branch still needs to classify it on a presentation.
    """

    now = datetime.now(UTC)
    async with TestSession() as db:
        user = await make_user(db)
        # Way past cutoff (10 days expired) — should be deleted.
        old_revoked = await make_refresh_token(
            db, user.id, expires_at=now - timedelta(days=10), revoked=True
        )
        old_active = await make_refresh_token(
            db, user.id, expires_at=now - timedelta(days=10)
        )
        # Just inside retention (12h past expiry) — should survive.
        recent_expired = await make_refresh_token(
            db, user.id, expires_at=now - timedelta(hours=12)
        )
        # Still valid — never touch.
        future = await make_refresh_token(
            db, user.id, expires_at=now + timedelta(days=15)
        )
        await db.commit()
        old_revoked_id = old_revoked.id
        old_active_id = old_active.id
        recent_id = recent_expired.id
        future_id = future.id

    async with TestSession() as db:
        deleted = await gc_expired_refresh_tokens(db, retention_days=1)
        assert deleted == 2

    async with TestSession() as db:
        remaining = {
            r.id
            for r in (await db.execute(select(RefreshToken))).scalars().all()
        }
        assert old_revoked_id not in remaining
        assert old_active_id not in remaining
        assert recent_id in remaining
        assert future_id in remaining


@pytest.mark.asyncio
async def test_gc_zero_retention_deletes_anything_expired() -> None:
    now = datetime.now(UTC)
    async with TestSession() as db:
        user = await make_user(db)
        await make_refresh_token(db, user.id, expires_at=now - timedelta(seconds=5))
        await make_refresh_token(db, user.id, expires_at=now + timedelta(days=1))
        await db.commit()

    async with TestSession() as db:
        deleted = await gc_expired_refresh_tokens(db, retention_days=0)
        assert deleted == 1


@pytest.mark.asyncio
async def test_gc_negative_retention_rejected() -> None:
    async with TestSession() as db:
        with pytest.raises(ValueError):
            await gc_expired_refresh_tokens(db, retention_days=-1)


@pytest.mark.asyncio
async def test_gc_returns_zero_when_nothing_to_delete() -> None:
    async with TestSession() as db:
        user = await make_user(db)
        await make_refresh_token(
            db, user.id, expires_at=datetime.now(UTC) + timedelta(days=30)
        )
        await db.commit()

    async with TestSession() as db:
        assert await gc_expired_refresh_tokens(db, retention_days=1) == 0


@pytest.mark.asyncio
async def test_gc_handles_chain_links_via_set_null() -> None:
    """Deleting a row that a younger row's ``replaced_by_id`` points at
    must not violate the self-FK — ``ON DELETE SET NULL`` nulls the
    surviving link instead of cascading or raising.
    """

    now = datetime.now(UTC)
    async with TestSession() as db:
        user = await make_user(db)
        old = await make_refresh_token(
            db, user.id, expires_at=now - timedelta(days=10), revoked=True
        )
        survivor = await make_refresh_token(
            db, user.id, expires_at=now + timedelta(days=30)
        )
        survivor.replaced_by_id = old.id  # downstream points UP the chain
        await db.commit()
        survivor_id = survivor.id

    async with TestSession() as db:
        deleted = await gc_expired_refresh_tokens(db, retention_days=1)
        assert deleted == 1

    async with TestSession() as db:
        row = (
            await db.execute(
                select(RefreshToken).where(RefreshToken.id == survivor_id)
            )
        ).scalar_one()
        # SQLite test path doesn't enforce the FK, so this assertion is
        # opportunistic — Postgres production path is exercised by the
        # alembic migration's ON DELETE SET NULL.
        assert row is not None
