"""``GET|PUT /api/orgs/{org_id}/limits`` organization limit and member default (8.7, S08; H-06).

S08 "사용 한도": the member default monthly limit (``default_member_limit``, editable
with ``can_manage_policy``, at most the organization/company limit — else ``422
limit_exceeds_parent``) and this organization's use against the limit the company set
(read-only here; company admins set it with ``PUT /api/usage-limits?scope=org``).
Reading needs ``can_view_usage`` (org admins/auditors, company admins/auditors).
"""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlTarget, load_org_control
from app.authz.org_oversight import load_org_oversight
from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.authz.usage_limit_access import LimitTarget
from app.database import SessionTenant, bind_tenant
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.usage_limits import UsageLimitView
from app.services.tenants.versioning import etag
from app.tenancy.limit_settings import limit_view, version_of
from app.tenancy.limit_writes import LimitWrite, save_default_member_limit

router = APIRouter(prefix="/api/orgs/{org_id}/limits", tags=["organization administration"])


class OrgMemberDefaultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_member_limit: Decimal | None = Field(ge=0, max_digits=20, decimal_places=8)


async def _bound(db: AsyncSession, user: CurrentUser, tenant_id: UUID, org_id: UUID) -> LimitTarget:
    await bind_tenant(db, SessionTenant(tenant_id=tenant_id, user_id=user.id))
    return LimitTarget("org", org_id, tenant_id, org_id)


async def org_limit_reader(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LimitTarget:
    organization = await load_org_oversight(db, user, org_id, "can_view_usage")
    return await _bound(db, user, organization.tenant_id, organization.id)


async def org_limit_writer(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LimitTarget:
    # The save locks company → organization itself (``limit_writes``).
    target = OrgControlTarget(org_id, "can_manage_policy", lock_organization=False)
    control = await load_org_control(db, user, target)
    return await _bound(db, user, control.organization.tenant_id, control.organization.id)


@router.get("", response_model=UsageLimitView)
@authz_policy(SELF)
async def get_org_limits(
    response: Response,
    target: LimitTarget = Depends(org_limit_reader),
    db: AsyncSession = Depends(get_db),
) -> UsageLimitView:
    response.headers["ETag"] = etag(await version_of(db, target))
    return await limit_view(db, target)


@router.put("", response_model=UsageLimitView)
@authz_policy(GOVERNANCE)
async def put_org_limits(
    payload: OrgMemberDefaultUpdate,
    request: Request,
    response: Response,
    _csrf: None = Depends(verify_csrf),
    target: LimitTarget = Depends(org_limit_writer),
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageLimitView:
    write = LimitWrite(db, user, request, if_match)
    view = await save_default_member_limit(write, target, payload.default_member_limit)
    response.headers["ETag"] = etag(await version_of(db, target))
    await db.commit()
    return view
