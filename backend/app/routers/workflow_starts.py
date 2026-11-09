"""Schedules and webhooks of a workflow (workflow-starts-sharing-plan 3.1-3.2).

The management routes are the maker's own rows (``PRIVATE``). The two hook routes take no
session: the webhook's secret token is the only credential (``SHARE_TOKEN``), any mismatch is
the same 404, and each webhook is rate limited.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, SHARE_TOKEN, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.rate_limit import limiter
from app.schemas.workflow import (
    WorkflowHookRun,
    WorkflowHookStart,
    WorkflowScheduleIn,
    WorkflowScheduleOut,
    WorkflowScheduleUpdate,
    WorkflowWebhookCreated,
    WorkflowWebhookIn,
    WorkflowWebhookOut,
    WorkflowWebhookUpdate,
)
from app.workflows import starts

router = APIRouter(tags=["workflows"])

WORKFLOW = PRIVATE("workflows", param="workflow_id")
SCHEDULE = PRIVATE("workflow_schedules", param="schedule_id")
WEBHOOK = PRIVATE("workflow_webhooks", param="webhook_id")
HOOK_RATE = "30/minute"


def _draft(body: WorkflowScheduleIn) -> starts.ScheduleDraft:
    return starts.ScheduleDraft(body.name, body.kind, body.config, body.timezone, body.inputs)


def hook_key(request: Request) -> str:
    """One bucket per webhook address."""
    return f"workflow-hook:{request.path_params.get('public_id', '')}"


@router.get("/api/workflows/{workflow_id}/schedules", response_model=list[WorkflowScheduleOut])
@authz_policy(WORKFLOW)
async def list_schedules(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await starts.list_schedules(db, context, workflow_id)


@router.post(
    "/api/workflows/{workflow_id}/schedules",
    response_model=WorkflowScheduleOut,
    status_code=201,
)
@authz_policy(WORKFLOW)
async def create_schedule(
    workflow_id: uuid.UUID,
    body: WorkflowScheduleIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    row = await starts.create_schedule(db, context, workflow_id, _draft(body))
    await db.commit()
    return row


@router.patch("/api/workflow-schedules/{schedule_id}", response_model=WorkflowScheduleOut)
@authz_policy(SCHEDULE)
async def update_schedule(
    schedule_id: uuid.UUID,
    body: WorkflowScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    row = await starts.update_schedule(
        db,
        context,
        schedule_id,
        draft=_draft(body.schedule) if body.schedule else None,
        status=body.status,
    )
    await db.commit()
    return row


@router.delete("/api/workflow-schedules/{schedule_id}", status_code=204)
@authz_policy(SCHEDULE)
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await starts.delete_schedule(db, context, schedule_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/api/workflows/{workflow_id}/webhooks", response_model=list[WorkflowWebhookOut])
@authz_policy(WORKFLOW)
async def list_webhooks(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await starts.list_webhooks(db, context, workflow_id)


def _created(made: starts.NewWebhook) -> WorkflowWebhookCreated:
    return WorkflowWebhookCreated.model_validate(
        {**WorkflowWebhookOut.model_validate(made.row).model_dump(), "token": made.token}
    )


@router.post(
    "/api/workflows/{workflow_id}/webhooks",
    response_model=WorkflowWebhookCreated,
    status_code=201,
)
@authz_policy(WORKFLOW)
async def create_webhook(
    workflow_id: uuid.UUID,
    body: WorkflowWebhookIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    made = await starts.create_webhook(db, context, workflow_id, body.name)
    await db.commit()
    return _created(made)


@router.post("/api/workflow-webhooks/{webhook_id}/token", response_model=WorkflowWebhookCreated)
@authz_policy(WEBHOOK)
async def rotate_webhook_token(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    made = await starts.rotate_token(db, context, webhook_id)
    await db.commit()
    return _created(made)


@router.patch("/api/workflow-webhooks/{webhook_id}", response_model=WorkflowWebhookOut)
@authz_policy(WEBHOOK)
async def update_webhook(
    webhook_id: uuid.UUID,
    body: WorkflowWebhookUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    row = await starts.set_webhook_status(db, context, webhook_id, body.status)
    await db.commit()
    return row


@router.delete("/api/workflow-webhooks/{webhook_id}", status_code=204)
@authz_policy(WEBHOOK)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await starts.delete_webhook(db, context, webhook_id)
    await db.commit()
    return Response(status_code=204)


# --- the outside system's routes --------------------------------------------------------


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _hook_run(run: starts.WorkflowRun) -> WorkflowHookRun:
    return WorkflowHookRun(
        run_id=run.id,
        status=run.status,
        output=run.output,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.post("/hooks/workflows/{public_id}/runs", response_model=WorkflowHookRun, status_code=202)
@limiter.limit(HOOK_RATE, key_func=hook_key)
@authz_policy(SHARE_TOKEN())
async def hook_start(
    request: Request,
    public_id: str,
    body: WorkflowHookStart,
    authorization: str | None = Header(default=None),
):
    from app.database import async_session, tenant_session

    caller = await starts.authenticate(public_id, _bearer(authorization))
    async with tenant_session(caller.context, maker=async_session) as db:
        run = await starts.start_from_hook(db, caller, body.inputs)
        await db.commit()
        return _hook_run(run)


@router.get("/hooks/workflows/{public_id}/runs/{run_id}", response_model=WorkflowHookRun)
@limiter.limit(HOOK_RATE, key_func=hook_key)
@authz_policy(SHARE_TOKEN())
async def hook_read(
    request: Request,
    public_id: str,
    run_id: uuid.UUID,
    authorization: str | None = Header(default=None),
):
    from app.database import async_session, tenant_session

    caller = await starts.authenticate(public_id, _bearer(authorization))
    async with tenant_session(caller.context, maker=async_session) as db:
        return _hook_run(await starts.hook_run(db, caller, run_id))
