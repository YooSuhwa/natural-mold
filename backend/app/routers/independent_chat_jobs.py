from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import resume_not_found
from app.schemas.independent_chat_job import (
    IndependentJobCreate,
    IndependentJobOut,
    IndependentJobsOut,
)
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_queue_launch import launch_claimed_input
from app.services.conversation_run_worker import get_run_task_registry
from app.services.independent_job_cancellation import get_cancelable_job, request_job_cancellation
from app.services.independent_job_runs import describe_job
from app.services.independent_jobs import accept_independent_job, list_independent_jobs

router = APIRouter(tags=["conversations"])


@router.get("/api/conversations/{conversation_id}/independent-jobs")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_jobs(
    conversation_id: UUID,
    run_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> IndependentJobsOut:
    conversation = await owned_conversation(conversation_id, db, context)
    return await list_independent_jobs(db, conversation, context, run_id)


@router.post("/api/conversations/{conversation_id}/runs/{run_id}/independent-jobs", status_code=201)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def create_job(
    conversation_id: UUID,
    run_id: UUID,
    data: IndependentJobCreate,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> IndependentJobOut:
    conversation = await owned_conversation(conversation_id, db, context)
    accepted = await accept_independent_job(db, conversation, run_id, data, context)
    result = await describe_job(db, accepted.job, context)
    if result is None:
        raise resume_not_found()
    if accepted.input_id is None:
        response.status_code = 200
    else:
        await record_conversation_audit(
            db,
            user=user,
            request=request,
            action="conversation.independent_job_create",
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            metadata={"job_id": str(accepted.job.id), "parent_run_id": str(run_id)},
        )
    await db.commit()
    if accepted.input_id is not None:
        # Recovery owns the committed claimed input even if this process stops here.
        background.add_task(launch_claimed_input, accepted.input_id)
    return result


@router.post("/api/conversations/{conversation_id}/independent-jobs/{job_id}/cancel")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def cancel_job(
    conversation_id: UUID,
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> IndependentJobOut:
    conversation = await owned_conversation(conversation_id, db, context)
    job = await get_cancelable_job(db, conversation_id, job_id, context)
    was_canceled = job.cancel_requested_at is not None
    stop_id = await request_job_cancellation(db, job, context)
    if not was_canceled and job.cancel_requested_at is not None:
        await record_conversation_audit(
            db,
            user=user,
            request=request,
            action="conversation.independent_job_cancel",
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            metadata={"job_id": str(job.id)},
        )
    await db.flush()
    result = await describe_job(db, job, context)
    if result is None:
        raise resume_not_found()
    await db.commit()
    if stop_id is not None:
        get_run_task_registry().request_cancel(stop_id, reason="stop")
    return result
