from __future__ import annotations

import logging
import uuid

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        jobstores: dict = {}
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


def add_trigger_job(trigger_id: uuid.UUID, trigger_type: str, schedule_config: dict) -> None:
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
