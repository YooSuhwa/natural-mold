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
from dataclasses import replace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.authz.conversation_run_scope import owned_run_predicate
from app.authz.notification_delivery_scope import lock_active_notice_recipient
from app.authz.notifications import Target, notify
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.database import async_session, tenant_session
from app.models.agent import Agent
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob

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


async def record_independent_approvals(
    db: AsyncSession,
    *,
    job: IndependentChatJob,
    run: ConversationRun,
    context: OrgContext,
    secret_values: Iterable[str] | None = None,
) -> list[ApprovalRequest] | None:
    """Record the locked job's current interrupt, or return None until its checkpoint exists."""
    if (
        run.id != job.pending_approval_run_id
        or run.status != "interrupted"
        or job.cancel_requested_at is not None
        or run.conversation_id != job.child_conversation_id
        or run.agent_id != job.child_agent_id
        or (run.user_id, run.org_id, run.tenant_id)
        != (context.user_id, context.org_id, context.tenant_id)
        or (job.user_id, job.org_id, job.tenant_id)
        != (context.user_id, context.org_id, context.tenant_id)
    ):
        return []
    interrupts = await checkpoint_interrupts(job.child_conversation_id)
    if not run.interrupt_id or not any(i.interrupt_id == run.interrupt_id for i in interrupts):
        return None
    name = await db.scalar(
        select(Conversation.agent_name_snapshot).where(
            Conversation.id == run.conversation_id,
            Conversation.source == INDEPENDENT_JOB_SOURCE,
            private_scope_predicate(Conversation, context),
        )
    )
    if name is None:
        return []
    rows = await service.record_pending(
        db,
        run,
        interrupts,
        origin=service.RunOrigin(agent_name=name, source="subagent"),
        secret_values=secret_values,
    )
    await notify_requested(db, rows)
    return rows


async def _record_independent_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    context: OrgContext,
    secret_values: Iterable[str] | None,
) -> list[ApprovalRequest]:
    from app.services.independent_job_notifications import job_can_receive, lock_job_for_terminal
    from app.services.independent_job_runs import current_job_run

    job = await lock_job_for_terminal(db, run_id)
    if (
        job is None
        or job.pending_approval_run_id != run_id
        or not await job_can_receive(db, job, context)
    ):
        return []
    current = await current_job_run(db, job, context)
    if current is None or current.id != run_id or current.status != "interrupted":
        return []
    rows = await record_independent_approvals(
        db,
        job=job,
        run=current,
        context=context,
        secret_values=secret_values,
    )
    if rows is not None:
        job.pending_approval_run_id = None
        await db.commit()
    return rows or []


async def record_interrupted_run(
    run_id: uuid.UUID,
    context: OrgContext,
    *,
    secret_values: Iterable[str] | None = None,
    origin: service.RunOrigin | None = None,
) -> list[ApprovalRequest]:
    """Record the approval interrupts of a committed ``interrupted`` run (own session)."""
    async with tenant_session(context, maker=async_session) as db:
        target = (
            await db.execute(
                select(Conversation.id, Conversation.source, ConversationRun.created_at)
                .join(ConversationRun, Conversation.id == ConversationRun.conversation_id)
                .where(
                    ConversationRun.id == run_id,
                    ConversationRun.status == "interrupted",
                    private_scope_predicate(Conversation, context),
                    private_scope_predicate(ConversationRun, context),
                )
            )
        ).one_or_none()
        if target is None:
            return []
        if target.source == INDEPENDENT_JOB_SOURCE:
            return await _record_independent_run(db, run_id, context, secret_values)
        if not await lock_active_notice_recipient(db, context, nowait=False):
            return []
        # Admission locks the conversation before creating any new run. A resume can
        # already exist while its parent remains "interrupted" in permanent history.
        # Read the checkpoint only while this run still owns the conversation's turn.
        held = await db.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == target.id,
                Conversation.source == target.source,
                private_scope_predicate(Conversation, context),
            )
            .with_for_update()
        )
        if held is None:
            return []
        successor = await db.scalar(
            select(ConversationRun.id)
            .where(
                ConversationRun.conversation_id == held,
                owned_run_predicate(context),
                ConversationRun.id != run_id,
                or_(
                    ConversationRun.parent_run_id == run_id,
                    ConversationRun.created_at >= target.created_at,
                ),
            )
            .limit(1)
        )
        if successor is not None:
            return []
        run = await db.scalar(
            select(ConversationRun).where(
                ConversationRun.id == run_id,
                ConversationRun.conversation_id == held,
                ConversationRun.status == "interrupted",
                private_scope_predicate(ConversationRun, context),
            )
        )
        if run is None:
            return []
        interrupts = await checkpoint_interrupts(run.conversation_id)
        if not interrupts or (
            run.interrupt_id and all(i.interrupt_id != run.interrupt_id for i in interrupts)
        ):
            return []
        name = ""
        if run.agent_id is not None:
            name = await db.scalar(select(Agent.name).where(Agent.id == run.agent_id)) or ""
        base = origin or service.RunOrigin()
        rows = await service.record_pending(
            db,
            run,
            interrupts,
            origin=replace(base, agent_name=name),
            secret_values=secret_values or (),
        )
        unattended = [row for row in rows if row.source != "chat"]
        if unattended:
            await notify_requested(db, unattended)
        await db.commit()
        return rows


async def record_interrupted_run_best_effort(
    run_id: uuid.UUID, context: OrgContext, *, secret_values: Iterable[str] | None = None
) -> None:
    try:
        await record_interrupted_run(run_id, context, secret_values=secret_values)
    except Exception:
        logger.exception("approval request recording failed run_id=%s", run_id)


async def waiting_interrupts(conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
    """The approval interrupts the conversation's checkpoint is paused on (no questions)."""
    return [
        interrupt
        for interrupt in await checkpoint_interrupts(conversation_id)
        if service.approval_actions(interrupt.payload) is not None
    ]


async def pause(
    *,
    run_id: uuid.UUID,
    context: OrgContext,
    interrupts: list[service.PendingInterrupt],
    origin: service.RunOrigin,
    secret_values: Iterable[str],
) -> bool:
    """An unattended run waits for the inbox (17.3): mark the owned run ``interrupted`` (its
    worker slot is released, only the checkpoint remains) and record its requests with the
    envelope (schedule run or API run) that waits; ``False`` if the run is not ours."""
    from app.services.conversation_run_service import transition_run

    async with tenant_session(context, maker=async_session) as db:
        run = await db.get(ConversationRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return False
        await transition_run(db, run, "interrupted", interrupt_id=interrupts[-1].interrupt_id)
        await db.commit()
    await record_interrupted_run(run_id, context, secret_values=secret_values, origin=origin)
    return True


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
    "pause",
    "record_independent_approvals",
    "record_interrupted_run",
    "record_interrupted_run_best_effort",
    "waiting_interrupts",
]
