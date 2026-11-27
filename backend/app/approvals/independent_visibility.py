"""Bind background approval metadata and reminders to their still-owned delegation."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_agent_checks import current_authz
from app.authz.independent_job_history import job_history_candidates, readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.independent_chat_job import IndependentChatJob
from app.services.independent_job_runs import job_run_lineage


def _has_visible_job(context: OrgContext) -> ColumnElement[bool]:
    chain = job_run_lineage(context)
    jobs = job_history_candidates(context, current_authz()).subquery()
    return (
        select(chain.c.run_id)
        .join(jobs, jobs.c.job_id == chain.c.job_id)
        .join(IndependentChatJob, IndependentChatJob.id == chain.c.job_id)
        .where(
            IndependentChatJob.cancel_requested_at.is_(None),
            chain.c.run_id == ApprovalRequest.run_id,
            chain.c.conversation_id == ApprovalRequest.conversation_id,
            ApprovalRequest.org_id == context.org_id,
            ApprovalRequest.tenant_id == context.tenant_id,
            ApprovalRequest.subject_user_id == context.user_id,
            ApprovalRequest.decider_user_id == context.user_id,
        )
        .correlate(ApprovalRequest)
        .exists()
    )


async def readable_approval(db: AsyncSession, context: OrgContext) -> ColumnElement[bool]:
    independent = (
        select(Conversation.id)
        .where(
            Conversation.id == ApprovalRequest.conversation_id,
            Conversation.source == INDEPENDENT_JOB_SOURCE,
        )
        .correlate(ApprovalRequest)
        .exists()
    )
    if current_authz().enforces("agent"):
        try:
            confirmed = await authorized_independent_approval_ids(db, context, None)
        except AppError as exc:
            if exc.status < 500:
                raise
            return ~independent
        return or_(~independent, ApprovalRequest.id.in_(confirmed))
    return or_(~independent, _has_visible_job(context))


async def authorized_independent_approval_ids(
    db: AsyncSession,
    context: OrgContext,
    request_ids: Collection[UUID] | None,
) -> set[UUID]:
    if request_ids is not None and not request_ids:
        return set()
    query = (
        select(
            ApprovalRequest.id,
            IndependentChatJob.id.label("job_id"),
        )
        .join(
            IndependentChatJob,
            IndependentChatJob.child_conversation_id == ApprovalRequest.conversation_id,
        )
        .where(_has_visible_job(context))
    )
    if request_ids is not None:
        query = query.where(ApprovalRequest.id.in_(request_ids))
    rows = (await db.execute(query)).all()
    authz = current_authz()
    if not rows or not authz.enforces("agent"):
        return {row.id for row in rows}
    visible = await readable_job_conversations(
        db,
        context,
        job_ids={r.job_id for r in rows},
        authz=authz,
        propagate_outage=True,
    )
    return {row.id for row in rows if row.job_id in visible}


async def independent_job_approval_is_current(
    db: AsyncSession,
    row: ApprovalRequest,
    context: OrgContext,
) -> bool:
    """Ordinary rows keep their own policy; an independent child must still be waiting."""
    if row.kind != "tool" or row.conversation_id is None:
        return True
    source = await db.scalar(
        select(Conversation.source).where(Conversation.id == row.conversation_id)
    )
    if source != INDEPENDENT_JOB_SOURCE:
        return source is not None
    if row.id not in await authorized_independent_approval_ids(db, context, [row.id]):
        return False
    if row.status != "pending":
        return True
    from app.services.independent_job_runs import current_job_run

    job = await db.scalar(
        select(IndependentChatJob).where(
            IndependentChatJob.child_conversation_id == row.conversation_id,
            private_scope_predicate(IndependentChatJob, context),
        )
    )
    if job is None:
        return False
    run = await current_job_run(db, job, context)
    return run is not None and run.id == row.run_id and run.status == "interrupted"
