"""Approval inbox API (feature plan v2 순번 13, 17.2). Every route is ``SELF``.

* ``GET /api/approvals?status=pending|done``: the caller's requests to decide, in the
  current organization; newest first (pending: soonest to expire first).
* ``GET /api/approvals/pending-count``: ``{"count": n}`` for the navigation badge.
* ``POST /api/approvals/{id}/decision``: one decision per action; resumes the run.

Another person's request answers 404. A request already decided (here or in the chat),
expired, or whose run no longer waits with the same arguments answers 409.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import inbox
from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.models.approval_request import ApprovalRequest
from app.schemas.approval import ApprovalPage, ApprovalView, InboxDecisionRequest, PendingCount

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _mine(context: OrgContext):  # noqa: ANN202 - SQL expression list
    return (
        ApprovalRequest.decider_user_id == context.user_id,
        or_(ApprovalRequest.org_id == context.org_id, ApprovalRequest.org_id.is_(None)),
    )


@router.get("", response_model=ApprovalPage)
@authz_policy(SELF)
async def list_approvals(
    status: Literal["pending", "done"] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> ApprovalPage:
    query = select(ApprovalRequest).where(*_mine(context))
    if status == "pending":
        query = query.where(ApprovalRequest.status == "pending").order_by(
            ApprovalRequest.expires_at
        )
    else:
        query = query.where(ApprovalRequest.status != "pending").order_by(
            ApprovalRequest.decided_at.desc().nulls_last(), ApprovalRequest.created_at.desc()
        )
    rows = (await db.execute(query.limit(limit))).scalars()
    return ApprovalPage(items=[ApprovalView.model_validate(row) for row in rows])


@router.get("/pending-count", response_model=PendingCount)
@authz_policy(SELF)
async def pending_count(
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> PendingCount:
    count = await db.scalar(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(*_mine(context), ApprovalRequest.status == "pending")
    )
    return PendingCount(count=int(count or 0))


@router.post("/{approval_id}/decision", response_model=ApprovalView)
@authz_policy(SELF)
async def decide_approval(
    approval_id: UUID,
    body: InboxDecisionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> ApprovalView:
    try:
        row = await inbox.decide(
            db,
            approval_id=approval_id,
            decisions=[d.model_dump(exclude_none=True) for d in body.decisions],
            user=user,
            context=context,
            request=request,
        )
    except inbox.InboxError as exc:
        raise exc.as_app_error() from exc
    return ApprovalView.model_validate(row)
