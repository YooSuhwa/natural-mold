"""Bind each independent worker invocation to its durable child and approval chain."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.independent_job_authority import require_job_authority
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.tenancy.limit_usage import Subject, limit_states
from app.tenancy.limits import quota_error


def independent_job_unavailable() -> AppError:
    return AppError("independent_job_unavailable", "독립 작업을 실행할 수 없습니다", status=409)


async def _reaches_initial_run(
    db: AsyncSession, job: IndependentChatJob, run_id: UUID, *, interrupted: bool = False
) -> bool:
    # A user's new ordinary send in this child conversation has no authority
    # from the original delegation. Only its initial run and resumes from its
    # own interrupted attempts can reach the bound run. UNION deduplicates an
    # invalid cycle. Ownership applies to every hop, not just the last run.
    run = ConversationRun
    columns = (run.id, run.parent_run_id, run.source)
    scope = (
        run.conversation_id == job.child_conversation_id,
        run.agent_id == job.child_agent_id,
        run.user_id == job.user_id,
        run.org_id == job.org_id,
        run.tenant_id == job.tenant_id,
    )
    allowed = ("interrupted",) if interrupted else ("queued", "running")
    chain = (
        select(*columns)
        .where(
            *scope,
            run.id == run_id,
            run.is_active.is_(not interrupted),
            run.status.in_(allowed),
            run.cancel_requested_at.is_(None),
        )
        .cte("independent_resume_chain", recursive=True)
    )
    chain = chain.union(
        select(*columns)
        .join(chain, chain.c.parent_run_id == run.id)
        .where(*scope, chain.c.source == "resume", run.status == "interrupted")
    )
    return await db.scalar(select(chain.c.id).where(chain.c.id == job.child_run_id)) is not None


def _child_job(conversation_id: UUID, context: OrgContext) -> Select[tuple[IndependentChatJob]]:
    return (
        select(IndependentChatJob)
        .where(
            IndependentChatJob.child_conversation_id == conversation_id,
            private_scope_predicate(IndependentChatJob, context),
        )
        .execution_options(populate_existing=True)
    )


async def require_independent_resume_ready(
    db: AsyncSession, conversation_id: UUID, interrupted_run_id: UUID, context: OrgContext
) -> None:
    """Serialize resume against cancellation and recording the child approval."""
    query = _child_job(conversation_id, context)
    job = await db.scalar(query.with_for_update(skip_locked=True))
    if job is None:
        # A deleted delegation can never resume. A live recorder may hold the
        # job lock while updating both its marker and the approval inbox.
        if await db.scalar(query.with_only_columns(IndependentChatJob.id)) is None:
            raise independent_job_unavailable()
    elif job.cancel_requested_at is not None or not await _reaches_initial_run(
        db, job, interrupted_run_id, interrupted=True
    ):
        raise independent_job_unavailable()
    elif job.pending_approval_run_id is None:
        return
    raise AppError(
        "independent_job_approval_sync_pending",
        "승인 요청을 저장하고 있습니다",
        status=409,
        details={"retryable": True},
    )


async def authorize_independent_job_execution(
    db: AsyncSession,
    *,
    run_id: UUID,
    conversation_id: UUID,
    context: OrgContext,
) -> bool:
    """Refresh the delegation before preparing the child or admitting a new leaf.

    The caller owns the transaction. A marked child whose parent or job was
    deleted must keep failing closed even when its conversation survives.
    """
    source = await db.scalar(
        select(Conversation.source).where(
            Conversation.id == conversation_id, private_scope_predicate(Conversation, context)
        )
    )
    if source is None:
        raise independent_job_unavailable()
    if source != INDEPENDENT_JOB_SOURCE:
        return False
    job = await db.scalar(_child_job(conversation_id, context))
    if job is None or job.cancel_requested_at is not None:
        raise independent_job_unavailable()
    await require_job_authority(db, job, context)
    if not await _reaches_initial_run(db, job, run_id):
        raise independent_job_unavailable()
    # An independent worker can sit idle between sends. Read booked usage
    # afresh, even when ordinary model guards still have a below-threshold cache.
    subject = Subject(job.tenant_id, job.org_id, job.user_id, job.child_agent_id)
    for state in await limit_states(db, subject):
        if state.exceeded:
            raise quota_error(state, amounts=False)
    return True
