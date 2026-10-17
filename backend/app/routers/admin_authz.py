"""Operator authorization-sync screen HTTP boundary (plan 14.3, S27; H-04).

``SUPER`` in both deployment modes (S27 "권한"): ``require_super_user``, no company or
organization context. Every route reads through a platform session (``authz.ops``) so
cross-company reads keep working under RLS. Reads are side-effect free; the two actions
(outbox retry, reconcile run) are CSRF-checked first and audited in the same
transaction, refusals (409/404) included with ``outcome=denied``.
"""

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import ErrorCode, authorization_error
from app.authz.jobs import jobs_enabled
from app.authz.ops_failures import list_failures, retry_failure
from app.authz.ops_status import ops_status
from app.authz.policies import SUPER, authz_policy
from app.authz.reconcile_launch import Launcher, get_reconcile_launcher
from app.authz.reconcile_runs import claim_run, get_run, view
from app.config import settings
from app.database import platform_session
from app.dependencies import CurrentUser, require_super_user, verify_csrf
from app.exceptions import AppError
from app.schemas.admin_authz import (
    AuthzOpsStatus,
    OutboxFailure,
    OutboxFailurePage,
    ReconcileRunView,
    ReconcileStarted,
)
from app.services import audit_service

router = APIRouter(prefix="/api/admin/authz", tags=["platform authorization sync"])
Operator = Annotated[CurrentUser, Depends(require_super_user)]


async def get_ops_db() -> AsyncGenerator[AsyncSession, None]:
    """Platform scope for instance-wide reads (B-13 ratchet reason ``authz.ops``)."""
    async with platform_session("authz.ops") as db:
        yield db


Db = Annotated[AsyncSession, Depends(get_ops_db)]
Target = tuple[str, UUID | None]


async def _audit(
    db: AsyncSession,
    request: Request,
    actor: CurrentUser,
    action: str,
    target: Target,
    *,
    refused: str | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.id,
        actor_email_snapshot=actor.email,
        action=action,
        target_type=target[0],
        target_id=target[1],
        outcome="denied" if refused else "success",
        reason_code=refused,
        request=request,
    )


async def _refuse(
    db: AsyncSession,
    request: Request,
    actor: CurrentUser,
    action: str,
    target: Target,
    code: ErrorCode,
) -> AppError:
    """Record the refusal (own commit, nothing else was written) and return the error."""
    await db.rollback()
    await _audit(db, request, actor, action, target, refused=code)
    await db.commit()
    return authorization_error(code)


@router.get("/status", response_model=AuthzOpsStatus)
@authz_policy(SUPER)
async def get_status(db: Db, _actor: Operator) -> AuthzOpsStatus:
    return await ops_status(db, settings)


@router.get("/failures", response_model=OutboxFailurePage)
@authz_policy(SUPER)
async def get_failures(
    db: Db, _actor: Operator, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> OutboxFailurePage:
    return OutboxFailurePage(items=await list_failures(db, limit=limit))


@router.post(
    "/failures/{outbox_id}/retry",
    response_model=OutboxFailure,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def retry_outbox_failure(
    outbox_id: UUID, request: Request, db: Db, actor: Operator
) -> OutboxFailure:
    action, target = audit_actions.AUTHZ_OUTBOX_RETRY, ("authz_outbox", outbox_id)
    if not jobs_enabled(settings):
        raise await _refuse(db, request, actor, action, target, "authz_sync_disabled")
    found = await retry_failure(db, outbox_id)
    if found is None:
        raise await _refuse(db, request, actor, action, target, "not_found")
    await _audit(db, request, actor, action, target)
    await db.commit()
    return found


@router.post(
    "/reconcile",
    response_model=ReconcileStarted,
    status_code=202,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def start_reconcile(
    request: Request,
    db: Db,
    actor: Operator,
    launch: Annotated[Launcher, Depends(get_reconcile_launcher)],
) -> ReconcileStarted:
    action = audit_actions.AUTHZ_RECONCILE_RUN
    if not jobs_enabled(settings):
        raise await _refuse(
            db, request, actor, action, ("authz_reconcile", None), "authz_sync_disabled"
        )
    run = await claim_run(db, trigger="manual", requested_by=actor.id)
    if run is None:
        refused = ("authz_reconcile", None)
        raise await _refuse(db, request, actor, action, refused, "authz_reconcile_running")
    run_id = run.id
    await _audit(db, request, actor, action, ("authz_reconcile", run_id))
    await db.commit()
    launch(run_id)
    return ReconcileStarted(job_id=run_id)


@router.get("/reconcile/{job_id}", response_model=ReconcileRunView)
@authz_policy(SUPER)
async def get_reconcile(job_id: UUID, db: Db, _actor: Operator) -> ReconcileRunView:
    run = await get_run(db, job_id)
    if run is None:
        raise authorization_error("not_found")
    return view(run)
