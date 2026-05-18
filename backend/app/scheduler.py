from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import async_session

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        jobstores: dict[str, Any] = {}
        try:
            jobstores["default"] = SQLAlchemyJobStore(url=settings.database_url_sync)
        except Exception:
            # Fallback to memory jobstore (e.g., in test environment)
            from apscheduler.jobstores.memory import MemoryJobStore

            jobstores["default"] = MemoryJobStore()
        _scheduler = AsyncIOScheduler(jobstores=jobstores)
    return _scheduler


def _job_id(trigger_id: uuid.UUID) -> str:
    return f"trigger_{trigger_id}"


def add_trigger_job(
    trigger_id: uuid.UUID, trigger_type: str, schedule_config: dict[str, Any]
) -> None:
    """Register a scheduled job for a trigger."""
    from app.agent_runtime.trigger_executor import execute_trigger

    scheduler = get_scheduler()
    if not scheduler.running:
        logger.debug("Scheduler not running; skipping job registration for %s", trigger_id)
        return

    job_id = _job_id(trigger_id)

    if trigger_type == "interval":
        minutes = schedule_config.get("interval_minutes", 10)
        scheduler.add_job(
            execute_trigger,
            IntervalTrigger(minutes=minutes),
            id=job_id,
            args=[str(trigger_id)],
            replace_existing=True,
        )
        logger.info("Scheduled trigger %s: every %d minutes", trigger_id, minutes)

    elif trigger_type == "cron":
        expr = schedule_config.get("cron_expression", "0 * * * *")
        scheduler.add_job(
            execute_trigger,
            CronTrigger.from_crontab(expr),
            id=job_id,
            args=[str(trigger_id)],
            replace_existing=True,
        )
        logger.info("Scheduled trigger %s: cron %s", trigger_id, expr)


def remove_trigger_job(trigger_id: uuid.UUID) -> None:
    """Remove a scheduled job for a trigger."""
    scheduler = get_scheduler()
    job_id = _job_id(trigger_id)
    try:
        scheduler.remove_job(job_id)
        logger.info("Removed trigger job %s", trigger_id)
    except Exception:
        logger.debug("Trigger job %s not found in scheduler", trigger_id)


def pause_trigger_job(trigger_id: uuid.UUID) -> None:
    """Pause a scheduled job for a trigger."""
    scheduler = get_scheduler()
    job_id = _job_id(trigger_id)
    try:
        scheduler.pause_job(job_id)
        logger.info("Paused trigger job %s", trigger_id)
    except Exception:
        logger.debug("Trigger job %s not found in scheduler", trigger_id)


def resume_trigger_job(trigger_id: uuid.UUID) -> None:
    """Resume a paused trigger job."""
    scheduler = get_scheduler()
    job_id = _job_id(trigger_id)
    try:
        scheduler.resume_job(job_id)
        logger.info("Resumed trigger job %s", trigger_id)
    except Exception:
        logger.debug("Trigger job %s not found in scheduler", trigger_id)


# ---------------------------------------------------------------------------
# Credential rotation
# ---------------------------------------------------------------------------

CREDENTIAL_ROTATION_JOB_ID = "credential_rotation"
_ROTATION_BATCH = 100


async def rotate_credentials_to_active_key() -> int:
    """Re-encrypt every credential whose ``key_id`` differs from the active key.

    Iterates in pages of ``_ROTATION_BATCH`` so a large backlog doesn't OOM
    a single transaction. Each row writes a ``rotate`` audit log; failures
    log+continue so a single bad row can't stall the rotation.
    """

    from sqlalchemy import select

    from app.credentials import service as credential_service
    from app.models.credential import Credential
    from app.security.key_provider import get_active_key_id

    active_key_id = get_active_key_id()
    rotated = 0

    while True:
        async with async_session() as db:
            result = await db.execute(
                select(Credential)
                .where(Credential.key_id != active_key_id)
                .limit(_ROTATION_BATCH)
            )
            rows = list(result.scalars().all())
            if not rows:
                return rotated
            for cred in rows:
                try:
                    await credential_service.re_encrypt_with_active_key(db, cred)
                    rotated += 1
                except Exception:  # noqa: BLE001 — keep rotation moving
                    logger.exception(
                        "credential %s rotation failed; will retry next run", cred.id
                    )
            await db.commit()
        if len(rows) < _ROTATION_BATCH:
            return rotated


def _register_cron_job(
    *,
    job_id: str,
    func: Any,
    cron_expr: str,
    cron_setting_name: str,
    log_label: str,
    log_extra: str = "",
) -> None:
    """Register a recurring cron-triggered job. Idempotent.

    Centralises the four-step shape every cron job repeats: scheduler-
    running guard, crontab parse with logged ``ValueError``, ``add_job``
    with the consistent dedup flags (``coalesce + max_instances=1``),
    and the success log. ``log_extra`` lets callers append job-specific
    detail (e.g. ``" (retention=1d)"``) without duplicating the rest.
    """

    scheduler = get_scheduler()
    if not scheduler.running:
        logger.debug("Scheduler not running; skipping %s registration", log_label)
        return
    try:
        trigger = CronTrigger.from_crontab(cron_expr)
    except ValueError:
        logger.exception(
            "invalid %s=%r; %s not scheduled", cron_setting_name, cron_expr, log_label
        )
        return
    scheduler.add_job(
        func,
        trigger,
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info("Scheduled %s: cron %s%s", log_label, cron_expr, log_extra)


def register_credential_rotation_job() -> None:
    _register_cron_job(
        job_id=CREDENTIAL_ROTATION_JOB_ID,
        func=rotate_credentials_to_active_key,
        cron_expr=settings.credential_rotation_cron,
        cron_setting_name="credential_rotation_cron",
        log_label="credential rotation job",
    )


# ---------------------------------------------------------------------------
# Model catalog updater
# ---------------------------------------------------------------------------

CATALOG_UPDATE_JOB_ID = "catalog_update"


async def update_model_catalog() -> dict[str, Any]:
    """Run the multi-source catalog refresh + 3-layer merge build."""

    from app.services.model_catalog_updater import update_catalog

    try:
        return await update_catalog()
    except Exception:  # noqa: BLE001 — keep cron alive
        logger.exception("model catalog update failed; will retry next run")
        return {"status": "error"}


def register_catalog_update_job() -> None:
    _register_cron_job(
        job_id=CATALOG_UPDATE_JOB_ID,
        func=update_model_catalog,
        cron_expr=settings.catalog_update_cron,
        cron_setting_name="catalog_update_cron",
        log_label="model catalog update",
    )


# ---------------------------------------------------------------------------
# Health check sweep
# ---------------------------------------------------------------------------

HEALTH_CHECK_JOB_ID = "health_check_sweep"


async def health_check_all_active() -> dict[str, int]:
    """Probe every active model + MCP server and write history rows.

    Errors during a single probe are swallowed by the service layer so the
    sweep keeps moving — the probe itself records ``unhealthy`` rather than
    aborting the cron run.
    """

    from app.services import health_check as health_check_service

    async with async_session() as db:
        return await health_check_service.check_all_active(db)


def register_health_check_job() -> None:
    _register_cron_job(
        job_id=HEALTH_CHECK_JOB_ID,
        func=health_check_all_active,
        cron_expr=settings.health_check_cron,
        cron_setting_name="health_check_cron",
        log_label="health check sweep",
    )


# ---------------------------------------------------------------------------
# Refresh-token GC (ADR-016 §4.2)
# ---------------------------------------------------------------------------

REFRESH_TOKEN_GC_JOB_ID = "refresh_token_gc"


async def refresh_token_gc_run() -> int:
    """Delete expired refresh-token rows past the retention window."""

    from app.services.refresh_token_gc import gc_expired_refresh_tokens

    async with async_session() as db:
        try:
            return await gc_expired_refresh_tokens(
                db, retention_days=settings.refresh_token_gc_retention_days
            )
        except Exception:  # noqa: BLE001 — keep cron alive
            logger.exception("refresh-token GC failed; will retry next run")
            return 0


def register_refresh_token_gc_job() -> None:
    _register_cron_job(
        job_id=REFRESH_TOKEN_GC_JOB_ID,
        func=refresh_token_gc_run,
        cron_expr=settings.refresh_token_gc_cron,
        cron_setting_name="refresh_token_gc_cron",
        log_label="refresh-token GC",
        log_extra=f" (retention={settings.refresh_token_gc_retention_days}d)",
    )


# ---------------------------------------------------------------------------
# Lightweight MCP health polling
# ---------------------------------------------------------------------------

MCP_HEALTH_JOB_ID = "mcp_health_poll"


async def poll_mcp_servers_health() -> dict[str, int]:
    """Run a quick connectivity probe against every enabled MCP server.

    Distinct from ``health_check_all_active`` (which writes a persistent
    history row): this job only refreshes the lightweight
    ``health_status`` / ``health_polled_at`` / ``health_message`` columns
    so the list view can show a fresh dot without paying for a full sweep.
    """

    from sqlalchemy import or_, select

    from app.mcp import discovery as mcp_discovery
    from app.models.mcp_server import McpServer

    counters = {"checked": 0, "ok": 0, "error": 0}
    polled_at = datetime.now(UTC).replace(tzinfo=None)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(McpServer).where(
                    or_(
                        McpServer.is_system.is_(True),
                        McpServer.status != "disabled",
                    )
                )
            )
        ).scalars().all()

        for server in rows:
            counters["checked"] += 1
            try:
                probe = await mcp_discovery.test_server(db, server)
            except Exception as exc:  # noqa: BLE001 — keep the sweep alive
                logger.exception(
                    "mcp health poll failed for server %s", server.id
                )
                server.health_status = "error"
                server.health_polled_at = polled_at
                server.health_message = str(exc)
                counters["error"] += 1
                continue

            server.health_polled_at = polled_at
            if probe.get("success"):
                server.health_status = "ok"
                server.health_message = None
                counters["ok"] += 1
            else:
                server.health_status = "error"
                server.health_message = probe.get("error")
                counters["error"] += 1

        await db.commit()

    logger.info("mcp health poll finished: %s", counters)
    return counters


def register_mcp_health_job() -> None:
    """Register the lightweight MCP health polling job. Idempotent.

    Interval is taken from ``settings.mcp_health_check_interval_minutes``;
    values <1 are clamped up so the scheduler doesn't degenerate into a busy
    loop on misconfiguration.
    """

    scheduler = get_scheduler()
    if not scheduler.running:
        logger.debug("Scheduler not running; skipping mcp health job registration")
        return
    minutes = max(int(settings.mcp_health_check_interval_minutes or 5), 1)
    scheduler.add_job(
        poll_mcp_servers_health,
        IntervalTrigger(minutes=minutes),
        id=MCP_HEALTH_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info("Scheduled mcp health poll: every %d minutes", minutes)


# ---------------------------------------------------------------------------
# W3-out M4 — EventBroker eviction
# ---------------------------------------------------------------------------

BROKER_EVICTION_JOB_ID = "broker_eviction"
_BROKER_EVICTION_INTERVAL_SECONDS = 60
_BROKER_EVICTION_TTL_SECONDS = 300


def evict_expired_brokers() -> None:
    """Drop closed brokers past TTL + force-close stale live brokers.

    Wraps ``event_broker.registry.evict_expired`` so APScheduler can target a
    module-level callable (lambda 는 SQLAlchemyJobStore 직렬화 불가).
    """
    from app.agent_runtime.event_broker import registry as broker_registry

    try:
        evicted = broker_registry.evict_expired(
            ttl_seconds=_BROKER_EVICTION_TTL_SECONDS
        )
        if evicted:
            logger.info("EventBroker GC evicted %d closed brokers", evicted)
    except Exception:  # noqa: BLE001 — keep cron alive
        logger.exception("EventBroker eviction failed; will retry next run")


def register_broker_eviction_job() -> None:
    """Register the recurring EventBroker GC job (W3-out M4). Idempotent.

    60s interval. ``evict_expired`` 가 (a) closed broker 중 TTL 경과한 것
    (b) 30분 초과 live broker 강제 close 두 단계를 수행한다 — 정상적인
    finally 블록 미수행으로 누락된 broker 가 메모리에 누적되는 것을 막는다.
    """

    scheduler = get_scheduler()
    if not scheduler.running:
        logger.debug("Scheduler not running; skipping broker eviction registration")
        return
    scheduler.add_job(
        evict_expired_brokers,
        IntervalTrigger(seconds=_BROKER_EVICTION_INTERVAL_SECONDS),
        id=BROKER_EVICTION_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled EventBroker eviction: every %ds (TTL=%ds)",
        _BROKER_EVICTION_INTERVAL_SECONDS,
        _BROKER_EVICTION_TTL_SECONDS,
    )
