"""Where runs meet the approval rows: after an interrupt, and when a resume is accepted.

* ``record_interrupted_run``: once a run's ``interrupted`` state is committed, its pending
  approval interrupts are read from the checkpoint (the arguments exactly as the run will
  execute them) and recorded; requests from runs without a person watching (schedules)
  notify the decider.
* ``claim_resume``: the chat card's resume and the inbox's decision both call it inside
  the transaction that creates the resumed run, so whichever comes first wins.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.authz.notifications import Target, notify
from app.authz.org_context import OrgContext
from app.database import async_session, tenant_session
from app.models.agent import Agent
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun

logger = logging.getLogger(__name__)

INBOX_LINK = "/approvals"


def decision_types(response: Any) -> list[str]:
    """The decision types of one interrupt answer (``{"decisions": [{type, ...}]}``)."""
    if not isinstance(response, Mapping):
        return []
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        return []
    return [str(d.get("type")) for d in decisions if isinstance(d, Mapping)]


async def checkpoint_interrupts(conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
    from app.agent_runtime.langgraph_pending_inputs import interrupt_payloads_from_checkpointer

    payloads = await interrupt_payloads_from_checkpointer(
        {"configurable": {"thread_id": str(conversation_id)}}
    )
    return [
        service.PendingInterrupt(p["interrupt_id"], p["payload"], tuple(p["namespace"]))
        for p in payloads
    ]


async def notify_requested(db: AsyncSession, rows: Iterable[ApprovalRequest]) -> None:
    for row in rows:
        await notify(
            db,
            kind="approval.requested",
            recipients=[row.decider_user_id],
            tenant_id=row.tenant_id,
            org_id=row.org_id,
            target=Target("approval_request", row.id, row.agent_name),
            payload={"source": row.source, "action_count": len(row.actions)},
            link=INBOX_LINK,
        )


async def record_interrupted_run(
    run_id: uuid.UUID,
    context: OrgContext,
    *,
    secret_values: Iterable[str] = (),
    origin: service.RunOrigin | None = None,
) -> list[ApprovalRequest]:
    """Record the approval interrupts of a committed ``interrupted`` run (own session)."""
    async with tenant_session(context, maker=async_session) as db:
        run = await db.get(ConversationRun, run_id)
        if run is None or run.status != "interrupted":
            return []
        interrupts = await checkpoint_interrupts(run.conversation_id)
        if not interrupts:
            return []
        name = ""
        if run.agent_id is not None:
            name = await db.scalar(select(Agent.name).where(Agent.id == run.agent_id)) or ""
        base = origin or service.RunOrigin()
        rows = await service.record_pending(
            db,
            run,
            interrupts,
            origin=service.RunOrigin(name, base.source, base.trigger_id, base.trigger_run_id),
            secret_values=secret_values,
        )
        unattended = [row for row in rows if row.source != "chat"]
        if unattended:
            await notify_requested(db, unattended)
        await db.commit()
        return rows


async def record_interrupted_run_best_effort(
    run_id: uuid.UUID, context: OrgContext, *, secret_values: Iterable[str] = ()
) -> None:
    try:
        await record_interrupted_run(run_id, context, secret_values=secret_values)
    except Exception:
        logger.exception("approval request recording failed run_id=%s", run_id)


async def claim_resume(
    db: AsyncSession,
    *,
    parent_run_id: uuid.UUID,
    responses: Mapping[str, Any],
    actor_id: uuid.UUID | None,
    resumed_run_id: uuid.UUID,
    channel: str = "chat",
) -> list[ApprovalRequest]:
    """Claim the rows a resume answers (interrupt id → answer); ``ApprovalStale`` if lost."""
    return await service.claim(
        db,
        service.Claim(
            run_id=parent_run_id,
            decisions={key: decision_types(value) for key, value in responses.items()},
            channel=channel,
            actor_id=actor_id,
            resumed_run_id=resumed_run_id,
        ),
    )


__all__ = [
    "INBOX_LINK",
    "checkpoint_interrupts",
    "claim_resume",
    "decision_types",
    "notify_requested",
    "record_interrupted_run",
    "record_interrupted_run_best_effort",
]
