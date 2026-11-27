from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.independent_job_history import readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import resume_not_found
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import utc_now_naive
from app.models.independent_chat_job import IndependentChatJob
from app.services.conversation_run_state import request_cancel_run
from app.services.independent_job_runs import current_job_run


async def get_cancelable_job(
    db: AsyncSession, conversation_id: UUID, job_id: UUID, context: OrgContext
) -> IndependentChatJob:
    # A replayed link cannot disclose child status after its delegation was revoked.
    # Resolve remote decisions before acquiring the mutation row lock.
    if job_id not in await readable_job_conversations(db, context, job_ids=[job_id]):
        raise resume_not_found()
    job = await db.scalar(
        select(IndependentChatJob)
        .where(
            private_scope_predicate(IndependentChatJob, context),
            IndependentChatJob.id == job_id,
            IndependentChatJob.parent_conversation_id == conversation_id,
        )
        .with_for_update()
    )
    if job is None:
        raise resume_not_found()
    return job


async def request_job_cancellation(
    db: AsyncSession, job: IndependentChatJob, context: OrgContext
) -> UUID | None:
    run = await current_job_run(db, job, context, lock=True)
    if run is None or run.status not in {"queued", "running", "canceling", "interrupted"}:
        return None
    job.cancel_requested_at = job.cancel_requested_at or utc_now_naive()
    if run.status == "interrupted":
        # An interrupted run is terminal; revoke its pending approvals and keep
        # the durable job cancellation so an already-accepted resume cannot act.
        await db.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.tenant_id == job.tenant_id,
                ApprovalRequest.org_id == job.org_id,
                ApprovalRequest.conversation_id == job.child_conversation_id,
                ApprovalRequest.run_id == run.id,
                ApprovalRequest.status == "pending",
            )
            .values(
                status="canceled",
                channel="chat",
                reason="independent_job_canceled",
                decided_at=datetime.now(UTC),
                decision_no=ApprovalRequest.decision_no + 1,
            )
        )
        return None
    await request_cancel_run(db, run, reason="stop")
    return run.id


async def cancel_children_for_parent(
    db: AsyncSession, *, conversation_id: UUID, run_id: UUID, context: OrgContext
) -> list[UUID]:
    jobs = (
        await db.scalars(
            select(IndependentChatJob)
            .where(
                private_scope_predicate(IndependentChatJob, context),
                IndependentChatJob.parent_conversation_id == conversation_id,
                IndependentChatJob.parent_run_id == run_id,
            )
            .order_by(IndependentChatJob.id)
            .with_for_update()
        )
    ).all()
    cancel_ids: list[UUID] = []
    for job in jobs:
        if (run_to_stop := await request_job_cancellation(db, job, context)) is not None:
            cancel_ids.append(run_to_stop)
    return cancel_ids
