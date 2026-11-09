"""Scheduled and webhook starts of a published workflow (workflow-starts-sharing-plan 3.1-3.2).

A schedule or webhook belongs to the person who made it; that person is the runner of every
run it starts, so the run checks that person's access exactly as a run started by hand.

Schedules fire from the leader's sweep (``fire_due``). A due row is claimed by moving its
``next_run_at`` forward with a compare-and-set on the value read, so one schedule fires once
even if two leaders overlap. A start the runner refuses (archived, unpublished, inputs that no
longer fit, a changed agent) counts as a failure; three in a row pause the schedule and tell
the person.

A webhook's token is shown once and only its SHA-256 is kept. The public routes find the
webhook by ``public_id`` and compare the token in constant time; any mismatch is the same 404.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.exceptions import AppError, ConflictError, NotFoundError
from app.models.workflow import WorkflowRun, WorkflowVersion
from app.models.workflow_start import WorkflowSchedule, WorkflowWebhook
from app.services import audit_service
from app.workflows import runner
from app.workflows.definition import WorkflowInput
from app.workflows.service import get_workflow, parse_definition

logger = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES: Final = 5
MAX_INTERVAL_MINUTES: Final = 31 * 24 * 60
MAX_ACTIVE_SCHEDULES: Final = 20
MAX_WEBHOOKS: Final = 5
PAUSE_AFTER_FAILURES: Final = 3
SWEEP_LIMIT: Final = 50
PAUSED_KIND: Final = "workflow.schedule_paused"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def schedule_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_SCHEDULE_NOT_FOUND", "예약을 찾을 수 없습니다")


def webhook_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_WEBHOOK_NOT_FOUND", "웹훅을 찾을 수 없습니다")


def _invalid(message: str, **details: Any) -> AppError:
    return AppError("WORKFLOW_START_INVALID", message, status=422, details=details or None)


# --- when a schedule fires -----------------------------------------------------------------


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _invalid("알 수 없는 시간대입니다", timezone=name) from exc


def check_timing(kind: str, config: dict[str, Any], timezone: str) -> dict[str, Any]:
    """The config in its stored form, or a 422 that says what is wrong."""
    zone = _zone(timezone)
    if kind == "interval":
        minutes = config.get("minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES
        ):
            raise _invalid(f"간격은 {MIN_INTERVAL_MINUTES}분 이상이어야 합니다")
        return {"minutes": minutes}
    if kind == "cron":
        from apscheduler.triggers.cron import CronTrigger

        expression = str(config.get("expression") or "").strip()
        try:
            CronTrigger.from_crontab(expression, timezone=zone)
        except ValueError as exc:
            raise _invalid("반복 시각 형식이 맞지 않습니다", expression=expression) from exc
        return {"expression": expression}
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(config.get("at") or ""))
        except ValueError as exc:
            raise _invalid("실행 시각 형식이 맞지 않습니다") from exc
        if at.tzinfo is None:
            at = at.replace(tzinfo=zone)
        if at.astimezone(UTC) <= datetime.now(UTC):
            raise _invalid("실행 시각이 이미 지났습니다")
        return {"at": at.isoformat()}
    raise _invalid("알 수 없는 예약 종류입니다", kind=kind)


def next_fire(kind: str, config: dict[str, Any], timezone: str, after: datetime) -> datetime | None:
    """The first fire time after ``after`` (naive UTC in and out); ``None``: never again."""
    zone = _zone(timezone)
    moment = after.replace(tzinfo=UTC)
    if kind == "interval":
        return after + timedelta(minutes=int(config["minutes"]))
    if kind == "cron":
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(str(config["expression"]), timezone=zone)
        fire = trigger.get_next_fire_time(None, moment + timedelta(seconds=1))
        return fire.astimezone(UTC).replace(tzinfo=None) if fire else None
    at = datetime.fromisoformat(str(config["at"])).astimezone(UTC).replace(tzinfo=None)
    return at if at > after else None


# --- inputs ----------------------------------------------------------------------------------


async def _published_inputs(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID
) -> list[WorkflowInput]:
    workflow = await get_workflow(db, context, workflow_id)
    if workflow.archived_at is not None:
        raise ConflictError("WORKFLOW_ARCHIVED", "보관한 워크플로입니다")
    if workflow.published_version_no is None:
        raise ConflictError("WORKFLOW_NOT_PUBLISHED", "게시한 버전이 없습니다")
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version_no == workflow.published_version_no,
        )
    )
    if version is None:
        raise ConflictError("WORKFLOW_NOT_PUBLISHED", "게시한 버전이 없습니다")
    return list(parse_definition(version.definition).inputs)


def _check_inputs(inputs: list[WorkflowInput], values: dict[str, Any]) -> dict[str, Any]:
    """S4: no file inputs here (nobody uploads); the rest as a run would check them."""
    if any(item.type == "file" and item.required for item in inputs):
        raise _invalid("파일 입력이 필요한 워크플로는 예약·웹훅으로 시작할 수 없습니다")
    problems = runner.input_problems(inputs, values)
    if problems:
        raise AppError(
            "WORKFLOW_INPUT_INVALID",
            "입력값을 확인하세요",
            status=422,
            details={"problems": problems},
        )
    return dict(values)


# --- schedules ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduleDraft:
    name: str
    kind: str
    config: dict[str, Any]
    timezone: str
    inputs: dict[str, Any]


async def list_schedules(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID
) -> list[WorkflowSchedule]:
    await get_workflow(db, context, workflow_id)
    stmt = (
        private_scoped(select(WorkflowSchedule), WorkflowSchedule, context)
        .where(WorkflowSchedule.workflow_id == workflow_id)
        .order_by(WorkflowSchedule.created_at, WorkflowSchedule.id)
    )
    return list((await db.execute(stmt)).scalars())


async def _active_count(db: AsyncSession, context: OrgContext) -> int:
    stmt = private_scoped(
        select(func.count()).select_from(WorkflowSchedule), WorkflowSchedule, context
    ).where(WorkflowSchedule.status == "active")
    return int(await db.scalar(stmt) or 0)


async def create_schedule(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, draft: ScheduleDraft
) -> WorkflowSchedule:
    workflow = await get_workflow(db, context, workflow_id)
    inputs = _check_inputs(await _published_inputs(db, context, workflow_id), draft.inputs)
    config = check_timing(draft.kind, draft.config, draft.timezone)
    if await _active_count(db, context) >= MAX_ACTIVE_SCHEDULES:
        raise ConflictError(
            "WORKFLOW_SCHEDULE_LIMIT", f"켜 둔 예약은 {MAX_ACTIVE_SCHEDULES}개까지입니다"
        )
    now = _now()
    row = WorkflowSchedule(
        workflow_id=workflow.id,
        user_id=context.user_id,
        org_id=workflow.org_id,
        tenant_id=workflow.tenant_id,
        name=draft.name,
        kind=draft.kind,
        config=config,
        timezone=draft.timezone,
        inputs=inputs,
        status="active",
        next_run_at=next_fire(draft.kind, config, draft.timezone, now),
    )
    db.add(row)
    await db.flush()
    return row


async def get_schedule(
    db: AsyncSession, context: OrgContext, schedule_id: uuid.UUID, *, for_update: bool = False
) -> WorkflowSchedule:
    stmt = private_scoped(
        select(WorkflowSchedule).where(WorkflowSchedule.id == schedule_id),
        WorkflowSchedule,
        context,
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise schedule_not_found()
    return row


async def update_schedule(
    db: AsyncSession,
    context: OrgContext,
    schedule_id: uuid.UUID,
    *,
    draft: ScheduleDraft | None = None,
    status: str | None = None,
) -> WorkflowSchedule:
    """Change the timing/inputs, or pause/resume. Resuming starts the count again."""
    row = await get_schedule(db, context, schedule_id, for_update=True)
    now = _now()
    if draft is not None:
        row.inputs = _check_inputs(
            await _published_inputs(db, context, row.workflow_id), draft.inputs
        )
        row.config = check_timing(draft.kind, draft.config, draft.timezone)
        row.name, row.kind, row.timezone = draft.name, draft.kind, draft.timezone
        if row.status == "active":
            row.next_run_at = next_fire(row.kind, row.config, row.timezone, now)
    if status is not None and status != row.status:
        if status == "active":
            await _published_inputs(db, context, row.workflow_id)
            if await _active_count(db, context) >= MAX_ACTIVE_SCHEDULES:
                raise ConflictError(
                    "WORKFLOW_SCHEDULE_LIMIT", f"켜 둔 예약은 {MAX_ACTIVE_SCHEDULES}개까지입니다"
                )
            row.next_run_at = next_fire(row.kind, row.config, row.timezone, now)
            if row.next_run_at is None:
                raise _invalid("다시 실행할 시각이 없습니다. 시각을 바꿔 주세요")
            row.failure_count, row.last_error = 0, None
        else:
            row.next_run_at = None
        row.status = status
    await db.flush()
    return row


async def delete_schedule(db: AsyncSession, context: OrgContext, schedule_id: uuid.UUID) -> None:
    row = await get_schedule(db, context, schedule_id, for_update=True)
    await db.delete(row)
    await db.flush()


@dataclass(frozen=True, slots=True)
class _Due:
    id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    next_run_at: datetime


async def _due(now: datetime) -> list[_Due]:
    from app.database import async_session, platform_session

    async with platform_session("workflow.schedule_sweep", maker=async_session) as db:
        rows = await db.execute(
            select(
                WorkflowSchedule.id,
                WorkflowSchedule.tenant_id,
                WorkflowSchedule.org_id,
                WorkflowSchedule.user_id,
                WorkflowSchedule.next_run_at,
            )
            .where(
                WorkflowSchedule.status == "active",
                WorkflowSchedule.next_run_at.is_not(None),
                WorkflowSchedule.next_run_at <= now,
            )
            .order_by(WorkflowSchedule.next_run_at)
            .limit(SWEEP_LIMIT)
        )
        return [_Due(r.id, r.tenant_id, r.org_id, r.user_id, r.next_run_at) for r in rows]


async def fire_one(
    db: AsyncSession, context: OrgContext, schedule_id: uuid.UUID, seen: datetime
) -> str:
    """Claim and start one due schedule: ``started``, ``failed``, ``paused`` or ``skipped``."""
    row = await db.get(WorkflowSchedule, schedule_id)
    if row is None or row.status != "active" or row.next_run_at != seen:
        return "skipped"
    now = _now()
    following = next_fire(row.kind, row.config, row.timezone, max(now, seen))
    claimed = await db.execute(
        update(WorkflowSchedule)
        .where(
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.status == "active",
            WorkflowSchedule.next_run_at == seen,
        )
        .values(
            next_run_at=following,
            status="active" if following is not None else "paused",
            last_run_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        await db.rollback()
        return "skipped"
    await db.commit()
    await db.refresh(row)
    try:
        run = await runner.start_run(
            db,
            context,
            row.workflow_id,
            dict(row.inputs),
            parent={"started_from": "schedule", "schedule_id": row.id},
        )
    except AppError as exc:
        await db.rollback()
        row = await db.get(WorkflowSchedule, schedule_id, with_for_update=True)
        if row is None:
            return "skipped"
        row.failure_count += 1
        row.last_error = exc.message[:500]
        outcome = "failed"
        if row.failure_count >= PAUSE_AFTER_FAILURES:
            row.status, row.next_run_at, outcome = "paused", None, "paused"
            await _notify_paused(db, row)
        await _audit_start(db, row, None, "failure", exc.code)
        await db.commit()
        return outcome
    row = await db.get(WorkflowSchedule, schedule_id, with_for_update=True)
    if row is not None:
        row.failure_count, row.last_error, row.last_run_id = 0, None, run.id
        await _audit_start(db, row, run, "success", None)
    await db.commit()
    return "started"


async def _audit_start(
    db: AsyncSession,
    row: WorkflowSchedule,
    run: WorkflowRun | None,
    outcome: str,
    reason: str | None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="system",
        actor_user_id=None,
        owner_user_id=row.user_id,
        action=runner.RUN_STARTED,
        target_type="workflow_run" if run else "workflow_schedule",
        target_id=run.id if run else row.id,
        target_owner_user_id=row.user_id,
        outcome=outcome,
        reason_code=reason,
        metadata={
            "workflow_id": str(row.workflow_id),
            "started_from": "schedule",
            "schedule_id": str(row.id),
            "version_id": str(run.version_id) if run else None,
        },
    )


async def _notify_paused(db: AsyncSession, row: WorkflowSchedule) -> None:
    from app.authz.notifications import Target, notify

    await notify(
        db,
        kind=PAUSED_KIND,
        recipients=[row.user_id],
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        target=Target("workflow_schedule", row.id, row.name),
        payload={"reason": (row.last_error or "")[:200]},
        link=f"/workflows/{row.workflow_id}/runs",
    )


async def fire_due() -> dict[str, int]:
    """The leader's sweep: every due schedule once, each in its own company session."""
    from app.database import async_session, tenant_session

    counts: dict[str, int] = {}
    for item in await _due(_now()):
        context = OrgContext(org_id=item.org_id, tenant_id=item.tenant_id, user_id=item.user_id)
        try:
            async with tenant_session(context, maker=async_session) as db:
                outcome = await fire_one(db, context, item.id, item.next_run_at)
        except Exception:
            logger.exception("workflow schedule %s did not fire", item.id)
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


# --- webhooks ------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NewWebhook:
    row: WorkflowWebhook
    token: str  # shown once


async def list_webhooks(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID
) -> list[WorkflowWebhook]:
    await get_workflow(db, context, workflow_id)
    stmt = (
        private_scoped(select(WorkflowWebhook), WorkflowWebhook, context)
        .where(WorkflowWebhook.workflow_id == workflow_id)
        .order_by(WorkflowWebhook.created_at, WorkflowWebhook.id)
    )
    return list((await db.execute(stmt)).scalars())


async def create_webhook(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, name: str
) -> NewWebhook:
    workflow = await get_workflow(db, context, workflow_id)
    if len(await list_webhooks(db, context, workflow_id)) >= MAX_WEBHOOKS:
        raise ConflictError("WORKFLOW_WEBHOOK_LIMIT", f"웹훅은 {MAX_WEBHOOKS}개까지입니다")
    token = f"mwh_{secrets.token_urlsafe(32)}"
    row = WorkflowWebhook(
        workflow_id=workflow.id,
        user_id=context.user_id,
        org_id=workflow.org_id,
        tenant_id=workflow.tenant_id,
        name=name,
        public_id=secrets.token_urlsafe(18),
        token_hash=_hash(token),
        token_hint=token[-4:],
        status="active",
    )
    db.add(row)
    await db.flush()
    return NewWebhook(row, token)


async def get_webhook(
    db: AsyncSession, context: OrgContext, webhook_id: uuid.UUID, *, for_update: bool = False
) -> WorkflowWebhook:
    stmt = private_scoped(
        select(WorkflowWebhook).where(WorkflowWebhook.id == webhook_id), WorkflowWebhook, context
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise webhook_not_found()
    return row


async def rotate_token(db: AsyncSession, context: OrgContext, webhook_id: uuid.UUID) -> NewWebhook:
    row = await get_webhook(db, context, webhook_id, for_update=True)
    token = f"mwh_{secrets.token_urlsafe(32)}"
    row.token_hash, row.token_hint = _hash(token), token[-4:]
    await db.flush()
    return NewWebhook(row, token)


async def set_webhook_status(
    db: AsyncSession, context: OrgContext, webhook_id: uuid.UUID, status: str
) -> WorkflowWebhook:
    row = await get_webhook(db, context, webhook_id, for_update=True)
    row.status = status
    await db.flush()
    return row


async def delete_webhook(db: AsyncSession, context: OrgContext, webhook_id: uuid.UUID) -> None:
    row = await get_webhook(db, context, webhook_id, for_update=True)
    await db.delete(row)
    await db.flush()


@dataclass(frozen=True, slots=True)
class HookCaller:
    webhook_id: uuid.UUID
    workflow_id: uuid.UUID
    context: OrgContext


async def authenticate(public_id: str, token: str | None) -> HookCaller:
    """The webhook for this address and token; the same 404 for any mismatch."""
    from app.database import async_session, platform_session

    async with platform_session("workflow.webhook", maker=async_session) as db:
        row = await db.scalar(select(WorkflowWebhook).where(WorkflowWebhook.public_id == public_id))
    expected = row.token_hash if row is not None else _hash("absent")
    given = _hash(token or "")
    if not hmac.compare_digest(expected, given) or row is None or row.status != "active":
        raise webhook_not_found()
    return HookCaller(
        row.id,
        row.workflow_id,
        OrgContext(org_id=row.org_id, tenant_id=row.tenant_id, user_id=row.user_id),
    )


async def start_from_hook(
    db: AsyncSession, caller: HookCaller, inputs: dict[str, Any]
) -> WorkflowRun:
    """Start a run as the webhook's maker (the same checks as a run started by hand)."""
    workflow_inputs = await _published_inputs(db, caller.context, caller.workflow_id)
    checked = _check_inputs(workflow_inputs, inputs)
    run = await runner.start_run(
        db,
        caller.context,
        caller.workflow_id,
        checked,
        parent={"started_from": "webhook", "webhook_id": caller.webhook_id},
    )
    await db.execute(
        update(WorkflowWebhook)
        .where(WorkflowWebhook.id == caller.webhook_id)
        .values(last_used_at=_now())
        .execution_options(synchronize_session=False)
    )
    await audit_service.record_event(
        db,
        actor_type="system",
        actor_user_id=None,
        owner_user_id=caller.context.user_id,
        action=runner.RUN_STARTED,
        target_type="workflow_run",
        target_id=run.id,
        target_owner_user_id=caller.context.user_id,
        outcome="success",
        metadata={
            "workflow_id": str(caller.workflow_id),
            "version_id": str(run.version_id),
            "started_from": "webhook",
            "webhook_id": str(caller.webhook_id),
        },
    )
    return run


async def hook_run(db: AsyncSession, caller: HookCaller, run_id: uuid.UUID) -> WorkflowRun:
    """A run this webhook started (anything else is the same 404)."""
    run = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id, WorkflowRun.webhook_id == caller.webhook_id
        )
    )
    if run is None:
        raise runner.run_not_found()
    return run


__all__ = [
    "HookCaller",
    "WorkflowRun",
    "NewWebhook",
    "ScheduleDraft",
    "authenticate",
    "check_timing",
    "create_schedule",
    "create_webhook",
    "delete_schedule",
    "delete_webhook",
    "fire_due",
    "fire_one",
    "get_schedule",
    "get_webhook",
    "hook_run",
    "list_schedules",
    "list_webhooks",
    "next_fire",
    "rotate_token",
    "set_webhook_status",
    "start_from_hook",
    "update_schedule",
]
