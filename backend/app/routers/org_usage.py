"""``GET /api/orgs/{org_id}/usage[/members|/agents]`` (plan 9.6 ``can_view_usage``, S26; H-03).

Organization admins/auditors and company admins/auditors. Non-members get 404, members
without the permission 403 ``permission_required``. The window defaults to the last 30
days and is clamped to 365 (same as ``/api/usage/daily``).
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_oversight import load_org_oversight
from app.authz.policies import SELF, authz_policy
from app.database import SessionTenant, bind_tenant
from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.org_usage import OrgAgentUsagePage, OrgMemberUsagePage, OrgUsageSummary
from app.services import org_usage
from app.services.usage_aggregate import resolve_window

router = APIRouter(prefix="/api/orgs/{org_id}/usage", tags=["organization administration"])


async def usage_org(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """The checked organization's id; the session is bound to its company."""
    organization = await load_org_oversight(db, user, org_id, "can_view_usage")
    await bind_tenant(db, SessionTenant(tenant_id=organization.tenant_id, user_id=user.id))
    return organization.id


def usage_window(
    from_: date | None = Query(None, alias="from", description="Inclusive start date."),
    to: date | None = Query(None, description="Inclusive end date (default: today)."),
) -> tuple[date, date]:
    return resolve_window(from_, to)


@router.get("", response_model=OrgUsageSummary)
@authz_policy(SELF)
async def get_org_usage(
    checked_org: UUID = Depends(usage_org),
    window: tuple[date, date] = Depends(usage_window),
    db: AsyncSession = Depends(get_db),
) -> OrgUsageSummary:
    totals = await org_usage.org_totals(db, checked_org, *window)
    return OrgUsageSummary(
        org_id=checked_org, from_date=window[0], to_date=window[1], **totals.model_dump()
    )


@router.get("/members", response_model=OrgMemberUsagePage)
@authz_policy(SELF)
async def get_org_usage_by_member(
    checked_org: UUID = Depends(usage_org),
    window: tuple[date, date] = Depends(usage_window),
    db: AsyncSession = Depends(get_db),
) -> OrgMemberUsagePage:
    items = await org_usage.org_usage_by_member(db, checked_org, *window)
    return OrgMemberUsagePage(
        org_id=checked_org, from_date=window[0], to_date=window[1], items=items
    )


@router.get("/agents", response_model=OrgAgentUsagePage)
@authz_policy(SELF)
async def get_org_usage_by_agent(
    checked_org: UUID = Depends(usage_org),
    window: tuple[date, date] = Depends(usage_window),
    db: AsyncSession = Depends(get_db),
) -> OrgAgentUsagePage:
    items = await org_usage.org_usage_by_agent(db, checked_org, *window)
    return OrgAgentUsagePage(
        org_id=checked_org, from_date=window[0], to_date=window[1], items=items
    )
