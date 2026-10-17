"""``GET /api/tenants/{tenant_id}/audit-events`` company activity (plan 9.7, 14.1, S26; H-03).

Company admins and auditors (``can_view_tenant_audit``) page the company's events with
an optional organization filter and see client IP / user agent. Non-members get 404,
members without the role 403. Daily CSV/JSON export (14.1, Q23) is not part of this route.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SELF, authz_policy
from app.database import bind_tenant
from app.dependencies import CurrentUser, get_current_user, get_db
from app.routers.audit_filters import AuditFilters, audit_filters, list_scoped
from app.schemas.audit import AuditEventPageResponse
from app.services.audit_listing import AuditScopeFilter
from app.tenancy.context import TenantContext, resolve_tenant_context
from app.tenancy.permissions import require_tenant_permission

router = APIRouter(prefix="/api/tenants/{tenant_id}/audit-events", tags=["company administration"])


async def tenant_audit_context(
    tenant_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    context = await resolve_tenant_context(db, user, tenant_id)
    require_tenant_permission(context, "can_view_tenant_audit")
    await bind_tenant(db, context)
    return context


@router.get("", response_model=AuditEventPageResponse)
@authz_policy(SELF)
async def list_tenant_audit_events(
    org_id: uuid.UUID | None = Query(default=None),
    filters: AuditFilters = Depends(audit_filters),
    context: TenantContext = Depends(tenant_audit_context),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AuditEventPageResponse:
    scope = AuditScopeFilter(tenant_id=context.tenant_id, org_id=org_id)
    return await list_scoped(db, user, filters, scope, mask_client=False)
