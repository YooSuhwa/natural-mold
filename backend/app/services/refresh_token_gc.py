"""Periodic GC for the ``refresh_tokens`` whitelist (ADR-016 §4.2).

Each successful rotation appends a new row and leaves the old one
revoked (so replay detection has something to recognise). Without GC
this table grows unbounded. We delete rows whose ``expires_at`` is
older than ``settings.refresh_token_gc_retention_days`` days — keeping
the replay-detection window intact while bounding storage.

Self-FK ``replaced_by_id`` is ``ON DELETE SET NULL``, so deleting a
mid-chain row safely nulls the link in surviving rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken

logger = logging.getLogger(__name__)


async def gc_expired_refresh_tokens(
    db: AsyncSession, *, retention_days: int
) -> int:
    """Delete refresh-token rows older than the retention cutoff.

    Returns the number of rows deleted. Retention is measured from
    ``expires_at`` (not ``revoked_at``), so still-valid tokens are
    never touched even if revoked early. Commits the transaction so
    the cron caller doesn't have to manage one.
    """

    if retention_days < 0:
        raise ValueError(f"retention_days must be >= 0, got {retention_days}")

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
    )
    await db.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info(
            "Refresh-token GC: deleted %d rows older than %s",
            deleted,
            cutoff.isoformat(),
        )
    return deleted
