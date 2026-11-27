"""Schedule delivery of committed chat notifications, one tenant transaction at a time."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.database import SessionTenant, async_session, platform_session, tenant_session
from app.scheduler import _leader_job_runner, get_scheduler, scheduler_leader_is_healthy
from app.scheduler_job_ids import LeaderSchedulerJobId

logger = logging.getLogger(__name__)
_TENANTS_PER_TICK = 16
_NOTICES_PER_TENANT = 50
_last_tenant: UUID | None = None


async def _due_tenant_ids(after: UUID | None) -> list[UUID]:
    from app.models.chat_notification_outbox import ChatNotificationOutbox as Outbox

    # Rotate through due companies even if one company has an unbounded backlog.
    query = (
        select(Outbox.tenant_id)
        .where(Outbox.status == "pending", Outbox.available_at <= datetime.now(UTC))
        .group_by(Outbox.tenant_id)
    )
    if after is not None:
        query = query.order_by(Outbox.tenant_id <= after)
    async with platform_session("scheduler.chat_notifications", maker=async_session) as db:
        return list(await db.scalars(query.order_by(Outbox.tenant_id).limit(_TENANTS_PER_TICK)))


async def drain_due_chat_notifications() -> None:
    from app.services.chat_notification_outbox import drain_chat_notification_outbox

    global _last_tenant  # fairness cursor only; delivery progress is durable in the outbox
    for tenant_id in await _due_tenant_ids(_last_tenant):
        if not await scheduler_leader_is_healthy():
            return
        _last_tenant = tenant_id
        try:
            async with tenant_session(SessionTenant(tenant_id), maker=async_session) as db:
                await drain_chat_notification_outbox(
                    db, tenant_id=tenant_id, limit=_NOTICES_PER_TENANT
                )
                await db.commit()
        except Exception as error:  # noqa: BLE001 - continue with the other companies
            logger.warning(
                "chat notification delivery failed tenant=%s error_type=%s",
                tenant_id,
                type(error).__name__,
            )


def register_chat_notification_job() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        job_id = LeaderSchedulerJobId.CHAT_NOTIFICATION_DRAIN
        scheduler.add_job(
            _leader_job_runner(),
            IntervalTrigger(seconds=5),
            id=job_id,
            args=[job_id],
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
