"""``GET /api/orgs/{org_id}/audit-events`` organization activity (plan 14.1, 9.6, S26; H-03).

``can_view_audit`` on the addressed organization (org admin/auditor, also without
membership, or company admin/auditor), independent of the active-org cookie. Same
filters and cursor as ``/api/audit-events``; client IP and user agent are masked.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_oversight import load_org_oversight
from app.authz.policies import SELF, authz_policy
from app.database import SessionTenant, bind_tenant
from app.dependencies import CurrentUser, get_current_user, get_db
from app.routers.audit_filters import AuditFilters, audit_filters, list_scoped
from app.schemas.audit import AuditEventPageResponse
from app.services.audit_listing import AuditScopeFilter

router = APIRouter(prefix="/api/orgs/{org_id}/audit-events", tags=["organization administration"])


async def org_audit_scope(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuditScopeFilter:
    organization = await load_org_oversight(db, user, org_id, "can_view_audit")
    await bind_tenant(db, SessionTenant(tenant_id=organization.tenant_id, user_id=user.id))
    return AuditScopeFilter(tenant_id=organization.tenant_id, org_id=organization.id)


@router.get("", response_model=AuditEventPageResponse)
@authz_policy(SELF)
async def list_org_audit_events(
    filters: AuditFilters = Depends(audit_filters),
    scope: AuditScopeFilter = Depends(org_audit_scope),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AuditEventPageResponse:
    return await list_scoped(db, user, filters, scope, mask_client=True)
