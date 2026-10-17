from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation

DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_CONVERSATION_POLICY = "schedule_thread"
VALID_TRIGGER_TYPES = {"interval", "cron", "one_time"}
VALID_STATUSES = {"active", "paused", "completed", "error"}
VALID_CONVERSATION_POLICIES = {"schedule_thread", "new_per_run", "selected_conversation"}
REQUIRES_FIXED_IDENTITY_MESSAGE = "agent identity_mode must be fixed for trigger execution"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_datetime(raw: Any, timezone: str) -> datetime:
    if not raw:
        raise ValueError("scheduled_at is required")
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone))
    return dt.astimezone(UTC).replace(tzinfo=None)


def _normalize_optional_datetime(raw: datetime | None) -> datetime | None:
    if raw is None:
        return None
    if raw.tzinfo is None:
        return raw
    return raw.astimezone(UTC).replace(tzinfo=None)


def _validate_positive_optional(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if int(value) < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return int(value)


def _validate_conversation_policy(policy: str | None) -> str:
    value = policy or DEFAULT_CONVERSATION_POLICY
    if value not in VALID_CONVERSATION_POLICIES:
        raise ValueError("invalid conversation_policy")
    return value


def normalize_schedule_config(
    trigger_type: str,
    schedule_config: dict[str, Any],
    timezone: str | None = None,
) -> tuple[dict[str, Any], str]:
    tz = timezone or str(schedule_config.get("timezone") or DEFAULT_TIMEZONE)
    ZoneInfo(tz)

    if trigger_type not in VALID_TRIGGER_TYPES:
        raise ValueError("invalid trigger_type")

    if trigger_type == "interval":
        raw_minutes = schedule_config.get("interval_minutes")
        if raw_minutes is None:
            raw_minutes = schedule_config.get("minutes")
        if raw_minutes is None:
            raise ValueError("interval_minutes must be >= 1")
        minutes = int(raw_minutes)
        if minutes < 1:
            raise ValueError("interval_minutes must be >= 1")
        return {"interval_minutes": minutes}, tz

    if trigger_type == "cron":
        expr = schedule_config.get("cron_expression") or schedule_config.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError("cron_expression is required")
        expr = expr.strip()
        CronTrigger.from_crontab(expr, timezone=ZoneInfo(tz))
        return {"cron_expression": expr}, tz

    scheduled_at = _parse_datetime(
        schedule_config.get("scheduled_at") or schedule_config.get("run_at"),
        tz,
    )
    if scheduled_at <= _now():
        raise ValueError("scheduled_at must be in the future")
    return {"scheduled_at": scheduled_at.isoformat() + "Z"}, tz


def _default_name(trigger_type: str, schedule_config: dict[str, Any]) -> str:
    if trigger_type == "interval":
        return f"매 {schedule_config.get('interval_minutes', 10)}분마다"
    if trigger_type == "cron":
        return f"Cron {schedule_config.get('cron_expression', '')}".strip()
    return "1회 실행"


async def sync_scheduler_for_trigger(db: AsyncSession, trigger: AgentTrigger) -> None:
    from app.scheduler import add_trigger_job, remove_trigger_job

    max_runs_reached = trigger.max_runs is not None and trigger.run_count >= trigger.max_runs
    end_at_reached = trigger.end_at is not None and trigger.end_at <= _now()
    if trigger.status != "active":
        remove_trigger_job(trigger.id)
        trigger.next_run_at = None
    elif max_runs_reached or end_at_reached:
        remove_trigger_job(trigger.id)
        trigger.status = "completed"
        trigger.next_run_at = None
    else:
        trigger.next_run_at = add_trigger_job(
            trigger.id,
            trigger.trigger_type,
            {**trigger.schedule_config, "timezone": trigger.timezone},
        )
    await db.commit()
    await db.refresh(trigger)


async def refresh_next_run_at(db: AsyncSession, trigger: AgentTrigger) -> None:
    from app.scheduler import get_trigger_job_next_run_at

    trigger.next_run_at = get_trigger_job_next_run_at(trigger.id)
    await db.commit()
    await db.refresh(trigger)


class TriggerRunInFlightError(Exception):
    """A run for this trigger is already in flight (SEC-3 duplicate guard)."""

    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__(f"trigger run {run_id} already in flight")


async def find_in_flight_run(db: AsyncSession, trigger_id: uuid.UUID) -> AgentTriggerRun | None:
    """Newest active or unacknowledged run for the trigger, if any."""
    result = await db.execute(
        select(AgentTriggerRun)
        .where(
            AgentTriggerRun.trigger_id == trigger_id,
            AgentTriggerRun.status.in_(("queued", "running", "canceling")),
        )
        .order_by(AgentTriggerRun.started_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def start_trigger_run(
    db: AsyncSession,
    trigger: AgentTrigger,
    *,
    source: str = "scheduled",
) -> AgentTriggerRun:
    run = AgentTriggerRun(
        trigger_id=trigger.id,
        agent_id=trigger.agent_id,
        trigger_name_snapshot=trigger.name,
        tenant_id=trigger.tenant_id,
        user_id=trigger.user_id,
        input_message=trigger.input_message,
        status="queued",
        source=source,
    )
    db.add(run)
    await db.flush()
    return run


async def finish_trigger_run(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    run: AgentTriggerRun,
    conversation: Conversation | None,
    status: str,
    error_message: str | None = None,
    output_preview: str | None = None,
    thread_id: str | None = None,
    checkpoint_id: str | None = None,
    trace_id: str | None = None,
    reason_code: str | None = None,
) -> None:
    finished_at = _now()
    run.status = status
    run.error_message = error_message
    run.reason_code = reason_code
    run.output_preview = output_preview[:1000] if output_preview else None
    run.finished_at = finished_at
    run.duration_ms = max(0, int((finished_at - run.started_at).total_seconds() * 1000))
    run.thread_id = thread_id
    run.checkpoint_id = checkpoint_id
    run.trace_id = trace_id
    if conversation is not None:
        run.conversation_id = conversation.id
    trigger.last_run_at = finished_at
    trigger.last_status = status
    trigger.last_error = error_message
    if status == "success":
        trigger.failure_count = 0
        trigger.run_count += 1
        if conversation is not None:
            conversation.unread_count += 1
            conversation.last_unread_at = finished_at
            conversation.last_activity_source = "schedule"
            conversation.updated_at = finished_at
        if trigger.trigger_type == "one_time" or (
            trigger.max_runs is not None and trigger.run_count >= trigger.max_runs
        ):
            trigger.status = "completed"
            trigger.next_run_at = None
    if status == "failed":
        trigger.failure_count += 1
        trigger.last_error = error_message
        if (
            trigger.auto_pause_after_failures is not None
            and trigger.failure_count >= trigger.auto_pause_after_failures
        ):
            trigger.status = "paused"
            trigger.next_run_at = None
    await db.commit()
    if trigger.status == "active":
        await refresh_next_run_at(db, trigger)


async def list_trigger_runs(
    db: AsyncSession, trigger_id: uuid.UUID, user_id: uuid.UUID
) -> list[AgentTriggerRun]:
    result = await db.execute(
        select(AgentTriggerRun)
        .where(
            AgentTriggerRun.trigger_id == trigger_id,
            AgentTriggerRun.user_id == user_id,
        )
        .order_by(AgentTriggerRun.started_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())
