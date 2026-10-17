"""``GET|PUT /api/usage-limits`` and ``GET /api/me/usage-limits`` (plan 8.7, 9.7; H-06).

``?scope=tenant|org|user|agent&id=`` (``org_id`` picks the organization of a user limit,
default: the current one). Who may read or set which scope: ``usage_limit_access``.
A value above the tightest higher limit is ``422 limit_exceeds_parent``; ``PUT`` needs
``If-Match`` with the ``ETag`` of the ``GET`` (``409 stale_version``). The caller's
own view (``SELF``) shows their limit, use and what is left this month.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.authz.usage_limit_access import LimitTarget, resolve_limit_target
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.schemas.usage_limits import LimitScope, MyUsageLimit, UsageLimitUpdate, UsageLimitView
from app.services.tenant_usage import my_usage_limit
from app.services.tenants.versioning import etag
from app.tenancy.limit_settings import limit_view, version_of
from app.tenancy.limit_writes import LimitWrite, save_limits

router = APIRouter(prefix="/api", tags=["usage limits"])


def _query(
    scope: LimitScope = Query(...),
    scope_id: UUID = Query(..., alias="id"),
    org_id: UUID | None = Query(default=None),
) -> tuple[LimitScope, UUID, UUID | None]:
    return scope, scope_id, org_id


async def read_target(
    query: tuple[LimitScope, UUID, UUID | None] = Depends(_query),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    db: AsyncSession = Depends(get_db),
) -> LimitTarget:
    scope, scope_id, org_id = query
    return await resolve_limit_target(db, user, scope, scope_id, current=context, org_id=org_id)


async def write_target(
    query: tuple[LimitScope, UUID, UUID | None] = Depends(_query),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    db: AsyncSession = Depends(get_db),
) -> LimitTarget:
    scope, scope_id, org_id = query
    return await resolve_limit_target(
        db, user, scope, scope_id, current=context, org_id=org_id, write=True
    )


@router.get("/usage-limits", response_model=UsageLimitView)
@authz_policy(SELF)
async def get_usage_limit(
    response: Response,
    target: LimitTarget = Depends(read_target),
    db: AsyncSession = Depends(get_db),
) -> UsageLimitView:
    response.headers["ETag"] = etag(await version_of(db, target))
    return await limit_view(db, target)


@router.put("/usage-limits", response_model=UsageLimitView)
@authz_policy(GOVERNANCE)
async def put_usage_limit(
    payload: UsageLimitUpdate,
    request: Request,
    response: Response,
    _csrf: None = Depends(verify_csrf),
    target: LimitTarget = Depends(write_target),
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageLimitView:
    view = await save_limits(LimitWrite(db, user, request, if_match), target, payload)
    response.headers["ETag"] = etag(await version_of(db, target))
    await db.commit()
    return view


@router.get("/me/usage-limits", response_model=MyUsageLimit)
@authz_policy(SELF)
async def get_my_usage_limit(
    context: OrgContext = Depends(get_org_context), db: AsyncSession = Depends(get_db)
) -> MyUsageLimit:
    return await my_usage_limit(db, context)
