"""Access request API (plan 9.7 "접근 요청", 8.4, 10.9, S18; H-01).

* ``POST /api/access-requests``: ``SELF``; the service asks ``min_visible`` itself —
  201 created, 200 the same pending request, 202 (no body) for a resource the caller
  cannot see or that does not exist.
* ``GET /api/access-requests?box=inbox|outbox|handled``: ``SELF``; inbox/handled rows
  are confirmed against ``can_share`` per page.
* ``POST /{request_id}/approve|deny``: ``can_share`` on the request's resource (404
  otherwise); approve shares through the E-01 path and applies its tuples before
  responding (8.6 item 2).
* ``POST /{request_id}/cancel``: the requester's own request.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.access_request_decisions import approve_request, deny_request
from app.authz.access_request_queries import list_requests, request_view
from app.authz.access_requests import cancel_request, create_request
from app.authz.delegation_sweep import revalidate_object
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, RES, SELF, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.access_request import (
    AccessRequestApprove,
    AccessRequestCreate,
    AccessRequestDeny,
    AccessRequestPage,
    AccessRequestView,
    RequestBox,
)

router = APIRouter(prefix="/api/access-requests", tags=["access requests"])
DECIDE = RES("{resource_type}", "can_share", param="request_id", min_visible="can_share")


@router.post(
    "",
    response_model=AccessRequestView,
    status_code=201,
    responses={202: {"description": "Accepted; nothing is revealed about the resource"}},
)
@authz_policy(SELF)
async def create_access_request(
    body: AccessRequestCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AccessRequestView | Response:
    result = await create_request(db, authz, context, body)
    if result.request is None:
        return Response(status_code=202)
    view = await request_view(db, result.request)
    await db.commit()
    response.status_code = 201 if result.created else 200
    return view


@router.get("", response_model=AccessRequestPage)
@authz_policy(SELF)
async def get_access_requests(
    box: RequestBox = "inbox",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AccessRequestPage:
    return await list_requests(db, authz, context, box, cursor=cursor, limit=limit)


@router.post("/{request_id}/approve", response_model=AccessRequestView)
@authz_policy(DECIDE)
async def approve_access_request(
    request_id: UUID,
    body: AccessRequestApprove,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AccessRequestView:
    request, change = await approve_request(db, authz, context, request_id, body)
    view = await request_view(db, request)
    await db.commit()
    await apply_lifecycle(change, authz)
    await revalidate_object(  # a stage change may narrow access (10.2, 10.6)
        authz,
        context.org_id,
        context.tenant_id,
        (view.resource_type, view.resource_id),
        trigger="grant_update",
    )
    return view


@router.post("/{request_id}/deny", response_model=AccessRequestView)
@authz_policy(DECIDE)
async def deny_access_request(
    request_id: UUID,
    body: AccessRequestDeny,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AccessRequestView:
    request = await deny_request(db, authz, context, request_id, body)
    view = await request_view(db, request)
    await db.commit()
    return view


@router.post("/{request_id}/cancel", response_model=AccessRequestView)
@authz_policy(PRIVATE("access_requests", param="request_id"))
async def cancel_access_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> AccessRequestView:
    request = await cancel_request(db, context, request_id)
    view = await request_view(db, request)
    await db.commit()
    return view
