"""A scheduled run that waits for the approval inbox (feature plan v2 순번 13, 17.3).

A schedule with ``approval_policy="inbox"`` runs with the standard approval interrupt. When
its invoke returns with the graph paused on an approval:

1. ``pause``: the run becomes ``interrupted`` (its worker slot is released, only the
   checkpoint remains), its requests are recorded with the schedule's ids and the decider
   is notified; the schedule run waits (``waiting``) and blocks the next firing.
2. The decision (inbox, chat card or expiry) resumes the run in the schedule conversation.
3. ``settle``: when that resumed run ends, the schedule run ends with it (``success`` /
   ``failed``; an expired request is ``failed`` with ``approval_expired``). A resumed run that
   stops for another approval keeps the schedule run waiting.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select

from app.approvals import recording, service
from app.authz.org_context import OrgContext
from app.database import async_session, tenant_session
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun

logger = logging.getLogger(__name__)

WAITING = "waiting"


@dataclass(frozen=True, slots=True)
class TriggerAwaitingApproval:
    """The invoke's outcome when the run paused for the approval inbox."""

    interrupt_id: str
    output: str


async def waiting_interrupts(conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
    return [
        interrupt
        for interrupt in await recording.checkpoint_interrupts(conversation_id)
        if service.approval_actions(interrupt.payload) is not None
    ]


async def pause(
    *,
    run_id: uuid.UUID,
    context: OrgContext,
    interrupts: list[service.PendingInterrupt],
    trigger_id: uuid.UUID,
    trigger_run_id: uuid.UUID,
    secret_values: Iterable[str],
) -> bool:
    """Mark the owned run ``interrupted`` and record its requests; ``False`` if not ours."""
    from app.services.conversation_run_service import transition_run

    async with tenant_session(context, maker=async_session) as db:
        run = await db.get(ConversationRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return False
        await transition_run(db, run, "interrupted", interrupt_id=interrupts[-1].interrupt_id)
        await db.commit()
    await recording.record_interrupted_run(
        run_id,
        context,
        secret_values=secret_values,
        origin=service.RunOrigin(
            source="trigger", trigger_id=trigger_id, trigger_run_id=trigger_run_id
        ),
    )
    return True


async def settle(run_id: uuid.UUID, status: str, context: OrgContext) -> None:
    """End the schedule run a resumed run answered (no-op for every other run)."""
    if status == "interrupted":
        return  # waiting again: the new requests carry the schedule run on
    from app.services import trigger_service

    async with tenant_session(context, maker=async_session) as db:
        row = (
            await db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.resumed_run_id == run_id,
                    ApprovalRequest.trigger_run_id.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or row.trigger_run_id is None:
            return
        trigger_run = await db.get(AgentTriggerRun, row.trigger_run_id, with_for_update=True)
        if trigger_run is None or trigger_run.status != WAITING:
            return
        expired = row.status == "expired"
        outcome = "success" if status == "completed" and not expired else "failed"
        error = "approval_expired" if expired else None if outcome == "success" else f"run {status}"
        trigger = (
            await db.get(AgentTrigger, trigger_run.trigger_id) if trigger_run.trigger_id else None
        )
        if trigger is None:
            trigger_run.status = outcome
            trigger_run.error_message = error
            await db.commit()
            return
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=trigger_run,
            conversation=await db.get(Conversation, row.conversation_id),
            status=outcome,
            error_message=error,
            reason_code="approval_expired" if expired else None,
            thread_id=str(row.conversation_id),
        )


async def settle_best_effort(run_id: uuid.UUID, status: str, context: OrgContext) -> None:
    try:
        await settle(run_id, status, context)
    except Exception:
        logger.exception("schedule run settle failed run_id=%s", run_id)


__all__ = [
    "WAITING",
    "TriggerAwaitingApproval",
    "pause",
    "settle",
    "settle_best_effort",
    "waiting_interrupts",
]
