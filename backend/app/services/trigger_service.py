from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.schemas.trigger import TriggerCreate, TriggerUpdate

DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_CONVERSATION_POLICY = "schedule_thread"
VALID_TRIGGER_TYPES = {"interval", "cron", "one_time"}
VALID_STATUSES = {"active", "paused", "completed", "error"}


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


def _serialize_trigger(
    trigger: AgentTrigger,
    *,
    agent_name: str | None = None,
    conversation_title: str | None = None,
    conversation_unread_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "agent_id": trigger.agent_id,
        "name": trigger.name,
        "trigger_type": trigger.trigger_type,
        "schedule_config": trigger.schedule_config,
        "input_message": trigger.input_message,
        "timezone": trigger.timezone,
        "conversation_policy": trigger.conversation_policy,
        "schedule_conversation_id": trigger.schedule_conversation_id,
        "status": trigger.status,
        "last_run_at": trigger.last_run_at,
        "next_run_at": trigger.next_run_at,
        "last_status": trigger.last_status,
        "last_error": trigger.last_error,
        "run_count": trigger.run_count,
        "failure_count": trigger.failure_count,
        "max_runs": trigger.max_runs,
        "end_at": trigger.end_at,
        "auto_pause_after_failures": trigger.auto_pause_after_failures,
        "created_at": trigger.created_at,
        "updated_at": trigger.updated_at,
        "agent_name": agent_name,
        "schedule_conversation_title": conversation_title,
        "schedule_conversation_unread_count": conversation_unread_count,
    }


async def get_owned_agent(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> Agent | None:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_trigger(
    db: AsyncSession, trigger_id: uuid.UUID, user_id: uuid.UUID
) -> AgentTrigger | None:
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_triggers(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AgentTrigger, Agent.name, Conversation.title, Conversation.unread_count)
        .join(Agent, Agent.id == AgentTrigger.agent_id)
        .outerjoin(Conversation, Conversation.id == AgentTrigger.schedule_conversation_id)
        .where(AgentTrigger.agent_id == agent_id, AgentTrigger.user_id == user_id)
        .order_by(AgentTrigger.created_at.desc())
    )
    return [
        _serialize_trigger(
            trigger,
            agent_name=agent_name,
            conversation_title=conversation_title,
            conversation_unread_count=conversation_unread_count or 0,
        )
        for trigger, agent_name, conversation_title, conversation_unread_count in result.all()
    ]


async def list_user_triggers(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AgentTrigger, Agent.name, Conversation.title, Conversation.unread_count)
        .join(Agent, Agent.id == AgentTrigger.agent_id)
        .outerjoin(Conversation, Conversation.id == AgentTrigger.schedule_conversation_id)
        .where(AgentTrigger.user_id == user_id)
        .order_by(AgentTrigger.created_at.desc())
    )
    return [
        _serialize_trigger(
            trigger,
            agent_name=agent_name,
            conversation_title=conversation_title,
            conversation_unread_count=conversation_unread_count or 0,
        )
        for trigger, agent_name, conversation_title, conversation_unread_count in result.all()
    ]


async def create_trigger(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TriggerCreate,
) -> AgentTrigger:
    schedule_config, timezone = normalize_schedule_config(
        data.trigger_type,
        data.schedule_config,
        data.timezone,
    )
    name = (data.name or "").strip() or _default_name(data.trigger_type, schedule_config)
    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=user_id,
        name=name,
        trigger_type=data.trigger_type,
        schedule_config=schedule_config,
        input_message=data.input_message,
        timezone=timezone,
        conversation_policy=data.conversation_policy or DEFAULT_CONVERSATION_POLICY,
        max_runs=_validate_positive_optional(data.max_runs, "max_runs"),
        end_at=_normalize_optional_datetime(data.end_at),
        auto_pause_after_failures=_validate_positive_optional(
            data.auto_pause_after_failures,
            "auto_pause_after_failures",
        ),
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    await sync_scheduler_for_trigger(db, trigger)
    return trigger


async def update_trigger(
    db: AsyncSession, trigger: AgentTrigger, data: TriggerUpdate
) -> AgentTrigger:
    trigger_type = data.trigger_type or trigger.trigger_type
    timezone = data.timezone or trigger.timezone

    schedule_changed = (
        data.schedule_config is not None
        or data.trigger_type is not None
        or data.timezone is not None
    )
    if schedule_changed:
        schedule_config, timezone = normalize_schedule_config(
            trigger_type,
            data.schedule_config or trigger.schedule_config,
            timezone,
        )
        trigger.trigger_type = trigger_type
        trigger.schedule_config = schedule_config
        trigger.timezone = timezone

    if data.name is not None:
        trigger.name = data.name.strip() or _default_name(
            trigger.trigger_type,
            trigger.schedule_config,
        )
    if data.input_message is not None:
        trigger.input_message = data.input_message
    if data.conversation_policy is not None:
        trigger.conversation_policy = data.conversation_policy
    if data.status is not None:
        if data.status not in VALID_STATUSES:
            raise ValueError("invalid trigger status")
        trigger.status = data.status
    if data.max_runs is not None:
        trigger.max_runs = _validate_positive_optional(data.max_runs, "max_runs")
    if data.end_at is not None:
        trigger.end_at = _normalize_optional_datetime(data.end_at)
    if data.auto_pause_after_failures is not None:
        trigger.auto_pause_after_failures = _validate_positive_optional(
            data.auto_pause_after_failures,
            "auto_pause_after_failures",
        )

    await db.commit()
    await db.refresh(trigger)
    await sync_scheduler_for_trigger(db, trigger)
    return trigger


async def delete_trigger(db: AsyncSession, trigger: AgentTrigger) -> None:
    from app.scheduler import remove_trigger_job

    remove_trigger_job(trigger.id)
    await db.delete(trigger)
    await db.commit()


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


async def start_trigger_run(db: AsyncSession, trigger: AgentTrigger) -> AgentTriggerRun:
    run = AgentTriggerRun(
        trigger_id=trigger.id,
        agent_id=trigger.agent_id,
        user_id=trigger.user_id,
        input_message=trigger.input_message,
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def resolve_schedule_conversation(
    db: AsyncSession, trigger: AgentTrigger
) -> Conversation:
    conversation: Conversation | None = None
    if trigger.schedule_conversation_id is not None:
        conversation = await db.get(Conversation, trigger.schedule_conversation_id)

    if conversation is None:
        conversation = Conversation(
            agent_id=trigger.agent_id,
            title=f"스케줄: {trigger.name}",
            last_activity_source="schedule",
        )
        db.add(conversation)
        await db.flush()
        trigger.schedule_conversation_id = conversation.id
        await db.commit()
        await db.refresh(trigger)
        await db.refresh(conversation)
    return conversation


async def finish_trigger_run(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    run: AgentTriggerRun,
    conversation: Conversation | None,
    status: str,
    error_message: str | None = None,
) -> None:
    finished_at = _now()
    run.status = status
    run.error_message = error_message
    run.finished_at = finished_at
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


async def schedule_summary(db: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    rows = await list_user_triggers(db, user_id)
    return {
        "total_unread": sum(int(row["schedule_conversation_unread_count"] or 0) for row in rows),
        "active_count": sum(1 for row in rows if row["status"] == "active"),
    }
