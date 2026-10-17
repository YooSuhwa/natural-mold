"""``GET /api/tenants/{tenant_id}/usage`` and ``/limits`` (plan 9.7, 8.7, S26; H-06).

Company admins and auditors (``can_view_tenant_usage``): usage per organization for a
window (default the last 30 days, at most 365 — as ``/api/usage/daily``) and the company
limits with this month's use, member count, active runs and the organizations' allocated
total. Non-members 404, members without the role 403.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SELF, authz_policy
from app.authz.usage_limit_access import LimitTarget
from app.database import bind_tenant
from app.dependencies import CurrentUser, get_current_user, get_db
from app.routers.org_usage import usage_window
from app.schemas.usage_limits import TenantUsageSummary, UsageLimitView
from app.services.tenant_usage import tenant_usage
from app.tenancy.context import TenantContext, resolve_tenant_context
from app.tenancy.limit_settings import limit_view
from app.tenancy.permissions import require_tenant_permission

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["company administration"])


async def tenant_usage_context(
    tenant_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    context = await resolve_tenant_context(db, user, tenant_id)
    require_tenant_permission(context, "can_view_tenant_usage")
    await bind_tenant(db, context)
    return context


@router.get("/usage", response_model=TenantUsageSummary)
@authz_policy(SELF)
async def get_tenant_usage(
    window: tuple[date, date] = Depends(usage_window),
    context: TenantContext = Depends(tenant_usage_context),
    db: AsyncSession = Depends(get_db),
) -> TenantUsageSummary:
    return await tenant_usage(db, context.tenant_id, *window)


@router.get("/limits", response_model=UsageLimitView)
@authz_policy(SELF)
async def get_tenant_limits(
    context: TenantContext = Depends(tenant_usage_context),
    db: AsyncSession = Depends(get_db),
) -> UsageLimitView:
    return await limit_view(db, LimitTarget("tenant", context.tenant_id, context.tenant_id))
