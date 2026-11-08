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
4. ``abandon``: a request retired without a decision (an emergency stop, arguments that
   changed) resumes nothing; once no request of the schedule run is pending, it ends
   ``failed`` with the retirement reason instead of blocking the schedule forever.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.database import TenantScoped, async_session, tenant_session
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation

WAITING = "waiting"


@dataclass(frozen=True, slots=True)
class TriggerAwaitingApproval:
    """The invoke's outcome when the run paused for the approval inbox."""

    interrupt_id: str
    output: str


async def _end(
    db: AsyncSession,
    trigger_run: AgentTriggerRun,
    conversation_id: uuid.UUID,
    *,
    outcome: str,
    error: str | None,
    reason_code: str | None,
) -> None:
    from app.services import trigger_service

    trigger = await db.get(AgentTrigger, trigger_run.trigger_id) if trigger_run.trigger_id else None
    if trigger is None:
        trigger_run.status = outcome
        trigger_run.error_message = error
        await db.commit()
        return
    await trigger_service.finish_trigger_run(
        db,
        trigger=trigger,
        run=trigger_run,
        conversation=await db.get(Conversation, conversation_id),
        status=outcome,
        error_message=error,
        reason_code=reason_code,
        thread_id=str(conversation_id),
    )


async def settle(run_id: uuid.UUID, status: str, context: OrgContext) -> None:
    """End the schedule run a resumed run answered (no-op for every other run)."""
    if status == "interrupted":
        return  # waiting again: the new requests carry the schedule run on
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
        if trigger_run is None or trigger_run.status != WAITING or row.conversation_id is None:
            return  # schedule runs only wait on tool requests, which always have a conversation
        expired = row.status == "expired"
        outcome = "success" if status == "completed" and not expired else "failed"
        error = "approval_expired" if expired else None if outcome == "success" else f"run {status}"
        await _end(
            db,
            trigger_run,
            row.conversation_id,
            outcome=outcome,
            error=error,
            reason_code="approval_expired" if expired else None,
        )


async def abandon(tenant: TenantScoped, request_ids: Iterable[uuid.UUID]) -> None:
    """End the waiting schedule runs whose requests were all retired without a decision."""
    async with tenant_session(tenant, maker=async_session) as db:
        rows = list(
            (
                await db.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id.in_(list(request_ids)),
                        ApprovalRequest.trigger_run_id.is_not(None),
                        ApprovalRequest.status == "canceled",
                    )
                )
            ).scalars()
        )
        for trigger_run_id, row in {r.trigger_run_id: r for r in rows}.items():
            if await still_pending(db, ApprovalRequest.trigger_run_id == trigger_run_id):
                continue
            trigger_run = await db.get(AgentTriggerRun, trigger_run_id, with_for_update=True)
            if trigger_run is None or trigger_run.status != WAITING or row.conversation_id is None:
                continue
            reason = row.reason or "approval_canceled"
            await _end(
                db,
                trigger_run,
                row.conversation_id,
                outcome="failed",
                error=f"approval {reason}",
                reason_code=reason,
            )


async def still_pending(db: AsyncSession, waiting_on: ColumnElement[bool]) -> bool:
    return (
        await db.scalar(
            select(ApprovalRequest.id)
            .where(waiting_on, ApprovalRequest.status == "pending")
            .limit(1)
        )
    ) is not None


__all__ = ["WAITING", "TriggerAwaitingApproval", "abandon", "settle", "still_pending"]
