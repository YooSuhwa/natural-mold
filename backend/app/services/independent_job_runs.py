"""Only the initial independent run and its approval resumes form the job's result."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.schemas.independent_chat_job import IndependentJobOut, JobStatus


def _lineage(job_ids: Sequence[UUID], context: OrgContext) -> CTE:
    scope = (
        ConversationRun.tenant_id == context.tenant_id,
        ConversationRun.org_id == context.org_id,
        ConversationRun.user_id == context.user_id,
    )
    found = (
        select(
            IndependentChatJob.id.label("job_id"),
            ConversationRun.id.label("run_id"),
            ConversationRun.conversation_id,
            ConversationRun.agent_id,
            ConversationRun.parent_run_id,
            ConversationRun.status,
        )
        .join(ConversationRun, ConversationRun.id == IndependentChatJob.child_run_id)
        .join(Conversation, Conversation.id == IndependentChatJob.child_conversation_id)
        .where(
            IndependentChatJob.id.in_(job_ids),
            private_scope_predicate(IndependentChatJob, context),
            private_scope_predicate(Conversation, context),
            Conversation.source == INDEPENDENT_JOB_SOURCE,
            ConversationRun.conversation_id == Conversation.id,
            ConversationRun.agent_id == IndependentChatJob.child_agent_id,
            ConversationRun.source == "start",
            ConversationRun.parent_run_id.is_(None),
            *scope,
        )
        .cte(recursive=True)
    )
    # UNION terminates even when reading a malformed historical resume cycle.
    return found.union(
        select(
            found.c.job_id,
            ConversationRun.id,
            ConversationRun.conversation_id,
            ConversationRun.agent_id,
            ConversationRun.parent_run_id,
            ConversationRun.status,
        ).where(
            *scope,
            ConversationRun.parent_run_id == found.c.run_id,
            found.c.status == "interrupted",
            ConversationRun.source == "resume",
            ConversationRun.conversation_id == found.c.conversation_id,
            ConversationRun.agent_id == found.c.agent_id,
        )
    )


def _is_latest_resume(chain: CTE) -> ColumnElement[bool]:
    successor = chain.alias("successor")
    return (
        ~select(successor.c.run_id)
        .where(successor.c.job_id == chain.c.job_id, successor.c.parent_run_id == chain.c.run_id)
        .exists()
    )


async def current_job_run(
    db: AsyncSession, job: IndependentChatJob, context: OrgContext, *, lock: bool = False
) -> ConversationRun | None:
    chain = _lineage([job.id], context)
    query = (
        select(ConversationRun)
        .join(chain, chain.c.run_id == ConversationRun.id)
        .where(_is_latest_resume(chain))
        .order_by(ConversationRun.created_at.desc(), ConversationRun.id.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update(of=ConversationRun)
    return await db.scalar(query.execution_options(populate_existing=True))


async def describe_jobs(
    db: AsyncSession,
    jobs: Sequence[IndependentChatJob],
    context: OrgContext,
) -> list[IndependentJobOut]:
    if not jobs:
        return []
    chain = _lineage([job.id for job in jobs], context)
    # Evaluate concurrent siblings in the same database snapshot. Only the
    # latest validated resume contributes its status and destination run ID.
    ranked = (
        select(
            chain.c.job_id,
            chain.c.run_id,
            func.row_number()
            .over(
                partition_by=chain.c.job_id,
                order_by=(ConversationRun.created_at.desc(), ConversationRun.id.desc()),
            )
            .label("rank"),
        )
        .join(ConversationRun, ConversationRun.id == chain.c.run_id)
        .where(_is_latest_resume(chain))
        .subquery()
    )
    rows = await db.execute(
        select(ranked.c.job_id, ConversationRun, Conversation.agent_name_snapshot)
        .join(ConversationRun, ConversationRun.id == ranked.c.run_id)
        .join(Conversation, Conversation.id == ConversationRun.conversation_id)
        .where(ranked.c.rank == 1)
        .execution_options(populate_existing=True)
    )
    by_job = {ident: (run, name) for ident, run, name in rows}
    return [_job_view(job, *by_job[job.id]) for job in jobs if job.id in by_job]


async def describe_job(
    db: AsyncSession, job: IndependentChatJob, context: OrgContext
) -> IndependentJobOut | None:
    result = await describe_jobs(db, [job], context)
    return result[0] if result else None


def _job_view(job: IndependentChatJob, run: ConversationRun, child_name: str) -> IndependentJobOut:
    status: JobStatus
    match run.status:
        case "interrupted":
            status = "canceled" if job.cancel_requested_at else "waiting_for_approval"
        case "failed" if run.error_code == "prepared_run_lease_missing":
            status = "needs_review"
        case "stale":
            status = "needs_review"
        case "queued" | "running" | "canceling" | "completed" | "canceled" as ordinary:
            status = ordinary
        case _:
            status = "failed"
    return IndependentJobOut(
        id=job.id,
        parent_agent_id=job.parent_agent_id,
        parent_conversation_id=job.parent_conversation_id,
        parent_run_id=job.parent_run_id,
        child_agent_id=job.child_agent_id,
        child_agent_name=child_name,
        child_conversation_id=job.child_conversation_id,
        child_run_id=run.id,
        status=status,
        reason_code=(
            "independent_runtime_unavailable"
            if run.error_code == "independent_job_unsupported"
            else None
        ),
    )


async def notification_jobs_visible(
    db: AsyncSession, refs: dict[UUID, tuple[UUID, UUID]], context: OrgContext
) -> set[UUID]:
    if not refs:
        return set()
    jobs = await db.scalars(
        select(IndependentChatJob)
        .join(Conversation, Conversation.id == IndependentChatJob.parent_conversation_id)
        .where(
            IndependentChatJob.id.in_(refs),
            private_scope_predicate(Conversation, context),
            private_scope_predicate(IndependentChatJob, context),
        )
    )
    return {
        job.id
        for job in await describe_jobs(db, jobs.all(), context)
        if job.status == "completed"
        and refs[job.id] == (job.child_conversation_id, job.child_run_id)
    }


async def notification_job_is_visible(
    db: AsyncSession, *, job_id: UUID, conversation_id: UUID, run_id: UUID, context: OrgContext
) -> bool:
    return job_id in await notification_jobs_visible(
        db, {job_id: (conversation_id, run_id)}, context
    )
