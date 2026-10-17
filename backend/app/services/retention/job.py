"""The daily ``retention_sweep`` leader job body (plan 6.2, 8.8, 14.1; E-08).

Companies are listed in a platform session; each company is swept in its own
``tenant_session`` (scheduler work runs under the target row's company, 8.8), and one
company's failure is logged without stopping the others. ``app.scheduler`` keeps the
APScheduler wrapper (persisted job targets stay in that module).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionTenant, platform_session, tenant_session
from app.models.organization import Tenant
from app.schemas.retention import RetentionPreview
from app.services.retention.sweep import Leadership, SweepRequest, always_leader, sweep_tenant

logger = logging.getLogger(__name__)
SWEPT_TENANT_STATUSES = ("active", "suspended")  # deletion_scheduled belongs to I-07


async def run_retention_sweep(
    *,
    maker: Callable[[], AsyncSession] | None = None,  # tests pass their session factory
    now: datetime | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    still_leader: Leadership | None = None,
) -> dict[UUID, dict[UUID, RetentionPreview]]:
    moment = now or datetime.now(UTC)
    listing = (
        platform_session("scheduler.retention_gc")
        if maker is None
        else platform_session("scheduler.retention_gc", maker=maker)
    )
    async with listing as db:
        tenant_ids = list(
            await db.scalars(
                select(Tenant.id)
                .where(Tenant.status.in_(SWEPT_TENANT_STATUSES))
                .order_by(Tenant.id)
            )
        )
    results: dict[UUID, dict[UUID, RetentionPreview]] = {}
    for tenant_id in tenant_ids:
        try:
            scope = SessionTenant(tenant_id)
            session = tenant_session(scope) if maker is None else tenant_session(scope, maker=maker)
            async with session as db:
                results[tenant_id] = await sweep_tenant(
                    SweepRequest(db, moment, checkpointer, still_leader or always_leader), tenant_id
                )
        except Exception:  # noqa: BLE001 - keep the other companies' sweep alive
            logger.exception("retention sweep failed for tenant %s; retried tomorrow", tenant_id)
    return results
