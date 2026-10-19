"""I-07 leader jobs ``tenant_purge`` (daily) and ``tenant_export_gc`` (hourly).

The persisted job target stays ``scheduler_runtime.run_leader_scheduler_job`` (the
leader runner every cron job uses); this module only adds the body and the
registration so ``app.scheduler`` does not grow. Off by default
(``tenant_purge_enabled``): scheduling and cancelling a deletion work regardless, the
purge itself only runs once an operator enables the job (or runs the CLI). The export
GC always runs (it only cleans up exports operators asked for) and also once when this
process becomes leader, to fail exports a dead process left running.
"""

import logging
from datetime import UTC, datetime

from app.config import settings
from app.scheduler import _register_cron_job, get_scheduler, scheduler_leader_is_healthy
from app.scheduler_job_ids import LeaderSchedulerJobId

logger = logging.getLogger(__name__)


async def tenant_purge_run() -> None:
    from app.services.platform import tenant_purge_job

    try:
        await tenant_purge_job.run_tenant_purge_job(still_leader=scheduler_leader_is_healthy)
    except Exception:  # noqa: BLE001 - keep cron alive
        logger.exception("tenant purge failed; will retry next run")


def register_tenant_purge_job() -> None:
    scheduler = get_scheduler()
    job_id = LeaderSchedulerJobId.TENANT_PURGE
    if not settings.tenant_purge_enabled:
        if scheduler.running and scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)
        return
    _register_cron_job(
        job_id=job_id,
        cron_expr=settings.tenant_purge_cron,
        cron_setting_name="tenant_purge_cron",
        log_label="tenant purge",
    )


async def tenant_export_gc_run() -> None:
    from app.services.platform import tenant_export_gc

    try:
        await tenant_export_gc.run_export_gc()
    except Exception:  # noqa: BLE001 - keep cron alive
        logger.exception("tenant export gc failed; will retry next run")


def register_tenant_export_gc_job() -> None:
    job_id = LeaderSchedulerJobId.TENANT_EXPORT_GC
    _register_cron_job(
        job_id=job_id,
        cron_expr=settings.tenant_export_gc_cron,
        cron_setting_name="tenant_export_gc_cron",
        log_label="tenant export gc",
    )
    job = get_scheduler().get_job(job_id)
    if job is not None:  # restart recovery: first run right after becoming leader
        job.modify(next_run_time=datetime.now(UTC))
