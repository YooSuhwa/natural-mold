"""Continuous scheduler leadership and shared-trigger reconciliation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import assert_never

import anyio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.scheduler import (
    add_trigger_job,
    build_trigger_schedule_fingerprint,
    cleanup_skill_runtime_roots,
    draft_conversation_gc_run,
    evict_expired_brokers,
    get_scheduler,
    get_trigger_job_next_run_at,
    get_trigger_job_schedule_fingerprint,
    health_check_all_active,
    orphan_attachment_gc_run,
    poll_mcp_servers_health,
    recover_conversation_queue,
    refresh_token_gc_run,
    register_broker_eviction_job,
    register_catalog_update_job,
    register_conversation_queue_recovery_job,
    register_conversation_run_stale_sweep_job,
    register_credential_rotation_job,
    register_draft_conversation_gc_job,
    register_health_check_job,
    register_mcp_health_job,
    register_orphan_attachment_gc_job,
    register_refresh_token_gc_job,
    register_skill_draft_gc_job,
    register_skill_runtime_cleanup_job,
    release_scheduler_leader,
    rotate_credentials_to_active_key,
    scheduler_leader_is_healthy,
    skill_draft_gc_run,
    stop_scheduler,
    sweep_stale_conversation_runs,
    try_acquire_scheduler_leader,
    update_model_catalog,
)
from app.scheduler_job_ids import LeaderSchedulerJobId

logger = logging.getLogger(__name__)


async def run_leader_scheduler_job(job_id: LeaderSchedulerJobId) -> None:
    """Fence and dispatch one persisted leader-owned maintenance job."""

    if not await scheduler_leader_is_healthy():
        logger.warning("Scheduler maintenance job skipped after leadership loss: %s", job_id)
        return
    match job_id:
        case LeaderSchedulerJobId.CREDENTIAL_ROTATION:
            await rotate_credentials_to_active_key()
        case LeaderSchedulerJobId.CATALOG_UPDATE | LeaderSchedulerJobId.CATALOG_BOOTSTRAP:
            await update_model_catalog()
        case LeaderSchedulerJobId.HEALTH_CHECK:
            await health_check_all_active()
        case LeaderSchedulerJobId.REFRESH_TOKEN_GC:
            await refresh_token_gc_run()
        case LeaderSchedulerJobId.DRAFT_CONVERSATION_GC:
            await draft_conversation_gc_run()
        case LeaderSchedulerJobId.SKILL_DRAFT_GC:
            await skill_draft_gc_run()
        case LeaderSchedulerJobId.ORPHAN_ATTACHMENT_GC:
            await orphan_attachment_gc_run()
        case LeaderSchedulerJobId.MCP_HEALTH:
            await poll_mcp_servers_health()
        case LeaderSchedulerJobId.CONVERSATION_QUEUE_RECOVERY:
            await recover_conversation_queue()
        case LeaderSchedulerJobId.CONVERSATION_RUN_STALE_SWEEP:
            await sweep_stale_conversation_runs()
        case LeaderSchedulerJobId.SKILL_RUNTIME_CLEANUP:
            cleanup_skill_runtime_roots()
        case LeaderSchedulerJobId.BROKER_EVICTION:
            evict_expired_brokers()
        case unreachable:
            assert_never(unreachable)


def _job_id(trigger_id: uuid.UUID) -> str:
    return f"trigger_{trigger_id}"


def _is_schedulable(trigger: AgentTrigger, now: datetime) -> bool:
    if trigger.status != "active":
        return False
    if trigger.max_runs is not None and trigger.run_count >= trigger.max_runs:
        return False
    return trigger.end_at is None or trigger.end_at > now


async def reconcile_trigger_jobs() -> int:
    """Make the leader's APScheduler jobs match shared trigger definitions."""

    scheduler = get_scheduler()
    if not scheduler.running:
        return 0
    async with async_session() as db:
        result = await db.execute(select(AgentTrigger))
        triggers = list(result.scalars().all())
        now = datetime.now(UTC).replace(tzinfo=None)
        schedulable_ids = {
            _job_id(trigger.id) for trigger in triggers if _is_schedulable(trigger, now)
        }
        stale_job_ids = {
            job.id
            for job in scheduler.get_jobs()
            if job.id.startswith("trigger_") and job.id not in schedulable_ids
        }
        for job_id in stale_job_ids:
            scheduler.remove_job(job_id)

        for trigger in triggers:
            if _is_schedulable(trigger, now):
                schedule_config = {**trigger.schedule_config, "timezone": trigger.timezone}
                schedule_fingerprint = build_trigger_schedule_fingerprint(
                    trigger.trigger_type,
                    schedule_config,
                )
                if get_trigger_job_schedule_fingerprint(trigger.id) == schedule_fingerprint:
                    trigger.next_run_at = get_trigger_job_next_run_at(trigger.id)
                else:
                    trigger.next_run_at = add_trigger_job(
                        trigger.id,
                        trigger.trigger_type,
                        schedule_config,
                    )
                continue
            if trigger.status == "active":
                trigger.status = "completed"
            trigger.next_run_at = None
        await db.commit()
        return len(schedulable_ids)


async def activate_scheduler() -> None:
    """Start the local scheduler and rebuild every leader-owned job."""

    scheduler = get_scheduler()
    scheduler_was_started = not scheduler.running
    if scheduler_was_started:
        scheduler.start(paused=True)
    register_credential_rotation_job()
    register_health_check_job()
    register_catalog_update_job()
    register_mcp_health_job()
    register_broker_eviction_job()
    register_conversation_run_stale_sweep_job()
    register_conversation_queue_recovery_job()
    register_refresh_token_gc_job()
    register_draft_conversation_gc_job()
    register_orphan_attachment_gc_job()
    register_skill_draft_gc_job()
    cleanup_skill_runtime_roots()
    register_skill_runtime_cleanup_job()
    trigger_count = await reconcile_trigger_jobs()
    if scheduler_was_started:
        scheduler.resume()
    logger.info("Scheduler leadership activated with %d active trigger(s)", trigger_count)


async def activate_scheduler_with_recovery() -> bool:
    """Activate leadership or release a partially initialised takeover."""

    try:
        await activate_scheduler()
    except SQLAlchemyError:
        stop_scheduler()
        await release_scheduler_leader()
        logger.warning("Scheduler activation failed; leadership released for retry", exc_info=True)
        return False
    return True


async def maintain_scheduler_leadership() -> bool:
    """Validate current leadership or atomically take over from a prior leader."""

    if await scheduler_leader_is_healthy():
        if get_scheduler().running:
            try:
                await reconcile_trigger_jobs()
            except SQLAlchemyError:
                logger.warning("Scheduler trigger reconciliation failed; will retry", exc_info=True)
        else:
            return await activate_scheduler_with_recovery()
        return True

    stop_scheduler()
    if not await try_acquire_scheduler_leader():
        return False
    return await activate_scheduler_with_recovery()


async def _monitor_scheduler_leadership() -> None:
    interval = max(settings.scheduler_leadership_poll_seconds, 1.0)
    while True:
        await anyio.sleep(interval)
        await maintain_scheduler_leadership()


@asynccontextmanager
async def scheduler_leadership_runtime() -> AsyncGenerator[None, None]:
    """Run continuous leadership checks for the FastAPI serving lifetime."""

    try:
        await maintain_scheduler_leadership()
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                _monitor_scheduler_leadership,
                name="scheduler-leadership-monitor",
            )
            try:
                yield
            finally:
                task_group.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            stop_scheduler()
            await release_scheduler_leader()
