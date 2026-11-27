"""Project the child's own terminal transition and reconcile its approval checkpoint."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.database import platform_session, tenant_session
from app.exceptions import AppError
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.services.independent_job_runs import current_job_run

logger = logging.getLogger(__name__)


async def lock_job_for_terminal(db: AsyncSession, run_id: UUID) -> IndependentChatJob | None:
    # Cancellation takes the job lock before its run lock. The worker follows the
    # same order so the committed decision wins even when both arrive together.
    return await db.scalar(
        select(IndependentChatJob)
        .join(Conversation, Conversation.id == IndependentChatJob.child_conversation_id)
        .join(ConversationRun, ConversationRun.conversation_id == Conversation.id)
        .where(
            ConversationRun.id == run_id,
            Conversation.source == INDEPENDENT_JOB_SOURCE,
            Conversation.user_id == IndependentChatJob.user_id,
            Conversation.tenant_id == IndependentChatJob.tenant_id,
            Conversation.org_id == IndependentChatJob.org_id,
            ConversationRun.user_id == IndependentChatJob.user_id,
            ConversationRun.tenant_id == IndependentChatJob.tenant_id,
            ConversationRun.org_id == IndependentChatJob.org_id,
            ConversationRun.agent_id == IndependentChatJob.child_agent_id,
        )
        .with_for_update(of=IndependentChatJob)
        .execution_options(populate_existing=True)
    )


async def job_can_receive(db: AsyncSession, job: IndependentChatJob, context: OrgContext) -> bool:
    if job.cancel_requested_at is not None or (job.user_id, job.org_id, job.tenant_id) != (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        return False
    try:
        await require_private_actor(db, context)
    except AppError as exc:
        if exc.status not in {403, 404, 409}:
            raise
        return False
    parent = await db.scalar(
        select(Conversation.id).where(
            Conversation.id == job.parent_conversation_id,
            private_scope_predicate(Conversation, context),
        )
    )
    return parent is not None


async def project_job_terminal(
    db: AsyncSession,
    run: ConversationRun,
    job: IndependentChatJob | None = None,
) -> None:
    if run.status not in {"completed", "interrupted"}:
        return
    if job is None:
        job = await lock_job_for_terminal(db, run.id)
    if job is None:
        return
    context = OrgContext(org_id=job.org_id, tenant_id=job.tenant_id, user_id=job.user_id)
    if not await job_can_receive(db, job, context):
        return
    current = await current_job_run(db, job, context)
    if current is None or current.id != run.id or current.status != run.status:
        return
    if run.status == "interrupted":
        job.pending_approval_run_id = run.id
        return

    from app.services.chat_notification_outbox import enqueue_independent_completion

    # The job delegates a request with no designated output file. Link to the
    # child's exact completed run, which retains its replies and artifact links.
    job.pending_approval_run_id = None
    await enqueue_independent_completion(
        db,
        job_id=job.id,
        tenant_id=job.tenant_id,
        org_id=job.org_id,
        user_id=job.user_id,
        conversation_id=job.child_conversation_id,
        run_id=run.id,
        admitted_with_notice=True,
    )


async def reconcile_job_approval(db: AsyncSession, job_id: UUID, context: OrgContext) -> bool:
    from app.approvals.recording import record_independent_approvals

    job = await db.scalar(
        select(IndependentChatJob)
        .where(
            IndependentChatJob.id == job_id,
            IndependentChatJob.pending_approval_run_id.is_not(None),
            private_scope_predicate(IndependentChatJob, context),
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if job is None:
        return False
    if await job_can_receive(db, job, context):
        run = await current_job_run(db, job, context)
        if run and run.id == job.pending_approval_run_id and run.status == "interrupted":
            rows = await record_independent_approvals(db, job=job, run=run, context=context)
            if rows is None:
                # The owning interrupt isn't visible yet. Retry on the next pass.
                return False
    job.pending_approval_run_id = None
    return True


async def recover_independent_job_approvals() -> None:
    # Read routing identifiers in index order, then access the checkpoint and
    # approval in the job owner's scoped session. An unavailable checkpoint
    # must not block later jobs or another tenant.
    after: UUID | None = None
    while True:
        query = (
            select(
                IndependentChatJob.id,
                IndependentChatJob.org_id,
                IndependentChatJob.tenant_id,
                IndependentChatJob.user_id,
            )
            .where(IndependentChatJob.pending_approval_run_id.is_not(None))
            .order_by(IndependentChatJob.id)
            .limit(64)
        )
        if after is not None:
            query = query.where(IndependentChatJob.id > after)
        async with platform_session("scheduler.independent_job_approvals") as platform:
            batch = (await platform.execute(query)).all()
        if not batch:
            return
        for job_id, org_id, tenant_id, user_id in batch:
            after = job_id
            context = OrgContext(org_id=org_id, tenant_id=tenant_id, user_id=user_id)
            try:
                async with tenant_session(context) as db:
                    if await reconcile_job_approval(db, job_id, context):
                        await db.commit()
            except Exception:
                logger.exception("Independent approval checkpoint unavailable for job=%s", job_id)
