"""The daily ``tenant_purge`` leader job body (plan S04 "만료일에 영구 삭제 작업"; I-07).

First finishes the post-commit cleanup of earlier purges (``tenant_purges`` receipts
still ``rows_deleted``), then lists the companies whose deletion is due and purges each
one (``tenant_purge.purge_tenant``) in its own sessions; one company's failure is logged
and retried on the next run without stopping the others. Leadership is re-checked
before each company.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import platform_sessions
from app.models.organization import Tenant
from app.services.platform.tenant_purge import (
    Leadership,
    PurgeResult,
    Sessions,
    always_leader,
    finish_cleanup,
    pending_cleanups,
    purge_tenant,
)
from app.services.tenants.versioning import utc_timestamp

logger = logging.getLogger(__name__)


def purge_sessions(maker: Callable[[], AsyncSession] | None) -> Sessions:
    if maker is None:
        return platform_sessions("tenant_lifecycle.purge")
    return platform_sessions("tenant_lifecycle.purge", maker=maker)


async def due_tenants(maker: Callable[[], AsyncSession] | None, now: datetime) -> list[UUID]:
    """Due companies, oldest due first (compared in UTC, so SQLite agrees with Postgres)."""
    async with purge_sessions(maker)() as db:
        rows = await db.execute(
            select(Tenant.id, Tenant.deletion_due_at).where(
                Tenant.status == "deletion_scheduled", Tenant.deletion_due_at.is_not(None)
            )
        )
        due = [(utc_timestamp(at), tenant_id) for tenant_id, at in rows.all() if at is not None]
    return [tenant_id for at, tenant_id in sorted(due) if at <= now]


async def run_tenant_purge_job(
    *,
    maker: Callable[[], AsyncSession] | None = None,  # tests pass their session factory
    now: datetime | None = None,
    still_leader: Leadership = always_leader,
) -> list[PurgeResult]:
    moment = now or datetime.now(UTC)
    sessions = purge_sessions(maker)
    for pending in await pending_cleanups(sessions):  # rows already gone, cleanup unfinished
        try:
            await finish_cleanup(sessions, pending)
        except Exception:  # noqa: BLE001 - retried next run
            logger.exception("tenant purge cleanup failed for %s; retried next run", pending)
    results: list[PurgeResult] = []
    for tenant_id in await due_tenants(maker, moment):
        if not await still_leader():
            break
        try:
            result = await purge_tenant(sessions, tenant_id, now=moment, still_leader=still_leader)
        except Exception:  # noqa: BLE001 - keep the other companies' purge alive
            logger.exception("tenant purge failed for %s; retried next run", tenant_id)
            continue
        if result is not None:
            results.append(result)
    return results
