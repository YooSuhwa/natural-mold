"""Keep the delegated result attached to its current owner until the bell commits."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import current_authz
from app.authz.independent_job_history import job_history_candidates
from app.authz.notification_delivery_scope import lock_active_notice_recipient
from app.authz.org_context import OrgContext
from app.models.independent_chat_job import IndependentChatJob
from app.services.independent_job_runs import current_job_run


async def lock_job_delivery(
    db: AsyncSession, context: OrgContext, *, job_id: UUID, conversation_id: UUID, run_id: UUID
) -> bool:
    # Remote grants have already been checked. Hold the job's local eligibility rows only
    # through this delivery transaction. A concurrent edit retries via the outbox savepoint.
    if not await lock_active_notice_recipient(db, context):
        return False
    eligible = job_history_candidates(context, current_authz()).with_only_columns(
        IndependentChatJob, maintain_column_froms=True
    )
    job = await db.scalar(
        eligible.where(
            IndependentChatJob.id == job_id,
            IndependentChatJob.child_conversation_id == conversation_id,
            IndependentChatJob.cancel_requested_at.is_(None),
        )
        .with_for_update(read=True, nowait=True)
        .execution_options(populate_existing=True)
    )
    if job is None:
        return False
    current = await current_job_run(db, job, context)
    return current is not None and current.id == run_id and current.status == "completed"
