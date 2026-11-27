"""Only the initial independent run and its approval resumes form the job's result."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE, Subquery

from app.authz.independent_job_history import readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.schemas.independent_chat_job import IndependentJobOut, JobStatus


def job_run_lineage(context: OrgContext, job_ids: Sequence[UUID] | None = None) -> CTE:
    """Reach only the initial child and its legitimate resumes in the exact private scope."""
    scope = (
        private_scope_predicate(ConversationRun, context),
        ConversationRun.run_source == "CHAT",
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
            private_scope_predicate(IndependentChatJob, context),
            private_scope_predicate(Conversation, context),
            Conversation.source == INDEPENDENT_JOB_SOURCE,
            ConversationRun.conversation_id == Conversation.id,
            ConversationRun.agent_id == IndependentChatJob.child_agent_id,
            ConversationRun.source == "start",
            ConversationRun.parent_run_id.is_(None),
            *scope,
        )
    )
    if job_ids is not None:
        found = found.where(IndependentChatJob.id.in_(job_ids))
    chain = found.cte(recursive=True)
    # UNION terminates even when reading a malformed historical resume cycle.
    return chain.union(
        select(
            chain.c.job_id,
            ConversationRun.id,
            ConversationRun.conversation_id,
            ConversationRun.agent_id,
            ConversationRun.parent_run_id,
            ConversationRun.status,
        ).where(
            *scope,
            ConversationRun.parent_run_id == chain.c.run_id,
            chain.c.status == "interrupted",
            ConversationRun.source == "resume",
            ConversationRun.conversation_id == chain.c.conversation_id,
            ConversationRun.agent_id == chain.c.agent_id,
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
    chain = job_run_lineage(context, [job.id])
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


def _ranked_job_runs(context: OrgContext, job_ids: Sequence[UUID]) -> Subquery:
    chain = job_run_lineage(context, job_ids)
    # Evaluate concurrent siblings in the same database snapshot. Only the
    # latest validated resume contributes its status and destination run ID.
    return (
        select(
            chain.c.job_id,
            chain.c.run_id,
            chain.c.conversation_id,
            chain.c.status,
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


async def describe_jobs(
    db: AsyncSession,
    jobs: Sequence[IndependentChatJob],
    context: OrgContext,
) -> list[IndependentJobOut]:
    if not jobs:
        return []
    ranked = _ranked_job_runs(context, [job.id for job in jobs])
    rows = await db.execute(
        select(ranked.c.job_id, ConversationRun, Conversation.agent_name_snapshot)
        .join(ConversationRun, ConversationRun.id == ranked.c.run_id)
        .join(Conversation, Conversation.id == ConversationRun.conversation_id)
        .where(ranked.c.rank == 1)
        .execution_options(populate_existing=True, autoflush=False)
    )
    by_job = {ident: (run, name) for ident, run, name in rows}
    return [_job_view(job, *by_job[job.id]) for job in jobs if job.id in by_job]


async def describe_readable_jobs(
    db: AsyncSession,
    jobs: Sequence[IndependentChatJob],
    context: OrgContext,
) -> list[IndependentJobOut]:
    visible = await readable_job_conversations(db, context, job_ids=[job.id for job in jobs])
    return await describe_jobs(db, [job for job in jobs if job.id in visible], context)


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


async def readable_independent_child_run_ids(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_ids: Collection[UUID] | None = None,
    context: OrgContext,
) -> set[UUID]:
    """Read only the original run and its resumes, optionally intersect a candidate page."""
    if run_ids is not None and not run_ids:
        return set()
    pairs = await readable_job_conversations(db, context, conversation_ids=[conversation_id])
    if not pairs:
        return set()
    chain = job_run_lineage(context, list(pairs))
    query = select(chain.c.run_id)
    if run_ids is not None:
        query = query.where(chain.c.run_id.in_(run_ids))
    return set(await db.scalars(query.execution_options(autoflush=False)))


async def can_read_independent_child_run(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    context: OrgContext,
) -> bool:
    return run_id in await readable_independent_child_run_ids(
        db,
        conversation_id=conversation_id,
        run_ids=[run_id],
        context=context,
    )


async def readable_independent_child_conversation_ids(
    db: AsyncSession,
    *,
    conversation_ids: Collection[UUID],
    context: OrgContext,
) -> set[UUID]:
    pairs = await readable_job_conversations(db, context, conversation_ids=conversation_ids)
    if not pairs:
        return set()
    initial = job_run_lineage(context, list(pairs))
    query = select(initial.c.conversation_id).where(initial.c.parent_run_id.is_(None))
    return set(await db.scalars(query.execution_options(autoflush=False)))


async def can_read_independent_child_conversation(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    context: OrgContext,
) -> bool:
    return conversation_id in await readable_independent_child_conversation_ids(
        db,
        conversation_ids=[conversation_id],
        context=context,
    )


async def notification_jobs_visible(
    db: AsyncSession,
    refs: dict[UUID, tuple[UUID, UUID]],
    context: OrgContext,
    *,
    propagate_outage: bool = False,
) -> set[UUID]:
    visible = await readable_job_conversations(
        db, context, job_ids=refs.keys(), propagate_outage=propagate_outage
    )
    if not visible:
        return set()
    ranked = _ranked_job_runs(context, list(visible))
    rows = await db.execute(
        select(ranked.c.job_id, ranked.c.conversation_id, ranked.c.run_id)
        .where(ranked.c.rank == 1, ranked.c.status == "completed")
        .execution_options(autoflush=False)
    )
    return {
        job_id
        for job_id, conversation_id, run_id in rows
        if refs[job_id] == (conversation_id, run_id)
    }


async def notification_job_is_visible(
    db: AsyncSession, *, job_id: UUID, conversation_id: UUID, run_id: UUID, context: OrgContext
) -> bool:
    return job_id in await notification_jobs_visible(
        db, {job_id: (conversation_id, run_id)}, context
    )
