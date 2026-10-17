"""BLOCKED trigger runs (plan 11.6; F-08).

At fire time the owner's ``agent#can_run`` (D-06), the owner's membership, the
agent state and the required links (F-03/F-06 ``agent_not_ready``) are decided
before any work. Outside legacy a denial no longer skips silently: a run is
recorded ``failed`` with ``reason_code`` (so ``finish_trigger_run``'s consecutive
failure auto pause applies unchanged), audited as ``run.blocked`` and handed to the
owner-notification hook. ``authz_unavailable`` is recorded ``skipped`` (never
pauses; the next fire retries) without an audit. Repeated blocks with the same reason
are recorded every time (so the auto pause counts them) but audited and notified
once per streak: only when the trigger's previous run did not end with that reason.
Legacy keeps the old log-and-skip exactly.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.run_error_codes import denial_error_code
from app.authz import runtime
from app.authz.dispatch_block import TRANSIENT, block_reason, blocked_message
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.services import trigger_schedule_service as schedule
from app.services.prepared_trigger_resolver import PreparedTriggerFailedError
from app.services.run_block_service import notify_owner_blocked, record_run_blocked

logger = logging.getLogger("app.agent_runtime.trigger_executor")


async def _previous_reason(db: AsyncSession, trigger: AgentTrigger, run: AgentTriggerRun):
    return await db.scalar(
        select(AgentTriggerRun.reason_code)
        .where(AgentTriggerRun.trigger_id == trigger.id, AgentTriggerRun.id != run.id)
        .order_by(AgentTriggerRun.started_at.desc(), AgentTriggerRun.created_at.desc())
        .limit(1)
    )


async def finish_blocked_trigger_run(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    run: AgentTriggerRun,
    reason: str,
    error: BaseException | None = None,
    message: str | None = None,
    conversation: Conversation | None = None,
    thread_id: str | None = None,
) -> None:
    typed = not _legacy()  # legacy keeps the plain failed run (F-03 message only)
    status = "skipped" if reason in TRANSIENT else "failed"
    code = getattr(error, "code", None)
    announce = typed and status == "failed" and await _previous_reason(db, trigger, run) != reason
    if announce:  # one audit + notification per streak of the same reason
        await record_run_blocked(
            db,
            invoker="schedule",
            reason=reason,
            agent_id=trigger.agent_id,
            owner_user_id=trigger.user_id,
            agent_owner_user_id=await db.scalar(
                select(Agent.user_id).where(Agent.id == trigger.agent_id)
            ),
            code=code if isinstance(code, str) else None,
            run_id=run.id,
            source_id=trigger.id,
        )
    await schedule.finish_trigger_run(
        db,
        trigger=trigger,
        run=run,
        conversation=conversation,
        status=status,
        error_message=message or blocked_message(reason, error),
        thread_id=thread_id,
        reason_code=reason if typed else None,
    )
    if announce:
        await notify_owner_blocked(
            kind="trigger.blocked",
            owner_user_id=trigger.user_id,
            reason=reason,
            source_id=trigger.id,
        )


def _legacy() -> bool:
    return runtime.runtime_authz().mode == "legacy"


def dispatch_reason(error: BaseException) -> str | None:
    """The BLOCKED reason of a dispatch denial outside legacy (legacy: ``None``)."""
    return None if _legacy() else block_reason(error)


def dispatch_failure_reason(error: BaseException) -> str | None:
    """Reason of a prepared pre-activation failure or lease denial; ``None`` = a run error."""
    if isinstance(error, PreparedTriggerFailedError) or denial_error_code(error):
        return dispatch_reason(error)
    return None


async def record_blocked_claim(
    db: AsyncSession, trigger_uuid: uuid.UUID, error: BaseException, *, force: bool
) -> AgentTriggerRun | None:
    """A claim-time denial outside legacy becomes a BLOCKED run; ``None`` = skip."""
    reason = dispatch_reason(error)
    if reason is None:
        return None
    await db.rollback()  # nothing of the refused claim is kept (locks released)
    trigger = await db.scalar(
        select(AgentTrigger)
        .where(AgentTrigger.id == trigger_uuid)
        .with_for_update()  # serialise with a concurrent fire/run-now before the check
        .execution_options(populate_existing=True)
    )
    if trigger is None or (trigger.status != "active" and not force):
        return None
    in_flight = await schedule.find_in_flight_run(db, trigger_uuid)
    if in_flight is not None:
        if force:
            raise schedule.TriggerRunInFlightError(in_flight.id)
        return None
    run = await schedule.start_trigger_run(db, trigger, source="run_now" if force else "scheduled")
    logger.warning("Trigger %s blocked at dispatch: %s", trigger_uuid, reason)
    await finish_blocked_trigger_run(db, trigger=trigger, run=run, reason=reason, error=error)
    await db.refresh(run)
    return run


__all__ = [
    "dispatch_failure_reason",
    "dispatch_reason",
    "finish_blocked_trigger_run",
    "record_blocked_claim",
]
