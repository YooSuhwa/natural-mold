"""Export job housekeeping (I-07, plan S04 "7일 유효"): the ``tenant_export_gc`` leader job.

* a queued or running job whose row has not moved for ``tenant_export_stale_minutes``
  (the process that ran it died: the background task is not durable) becomes
  ``failed`` / ``export_interrupted``, which also unblocks new exports and the purge;
* a finished archive past ``expires_at`` is deleted from disk and its ``storage_path``
  cleared (the job row stays as history, ``download_available`` false).

Runs at leader start-up (restart recovery) and then on ``tenant_export_gc_cron``.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import platform_session
from app.models.tenant_export import TenantExport
from app.services.platform.tenant_files import local_path
from app.services.tenants.versioning import utc_timestamp

logger = logging.getLogger(__name__)


def _session(maker: Callable[[], AsyncSession] | None) -> AsyncSession:
    if maker is None:
        return platform_session("tenant_lifecycle.export_gc")
    return platform_session("tenant_lifecycle.export_gc", maker=maker)


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


async def run_export_gc(
    *, maker: Callable[[], AsyncSession] | None = None, now: datetime | None = None
) -> tuple[int, int]:
    """``(interrupted jobs failed, expired archives deleted)``."""
    moment = now or datetime.now(UTC)
    stale = moment - timedelta(minutes=settings.tenant_export_stale_minutes)
    failed = expired = 0
    async with _session(maker) as db:
        rows = await db.scalars(
            select(TenantExport).where(TenantExport.status.in_(("queued", "running", "succeeded")))
        )
        for row in rows:
            if row.status in ("queued", "running"):
                if utc_timestamp(row.updated_at) <= stale:
                    row.status, row.error_code = "failed", "export_interrupted"
                    row.completed_at = moment
                    failed += 1
                continue
            due = row.expires_at
            if row.storage_path and due is not None and utc_timestamp(due) <= moment:
                if (path := local_path(row.storage_path)) is not None:
                    await run_sync(_unlink, path)
                row.storage_path = None
                expired += 1
        await db.commit()
    if failed or expired:
        logger.info("tenant export gc: %d interrupted, %d expired", failed, expired)
    return failed, expired
