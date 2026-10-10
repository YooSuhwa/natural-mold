"""Organization navigation and validated active organization selection."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import (
    OrganizationActor,
    OrgContext,
    active_memberships,
    resolve_org_context,
)
from app.authz.org_permissions import organization_permissions
from app.exceptions import AppError
from app.models.organization import Organization, Tenant
from app.models.user import User
from app.schemas.organization import OrganizationSummary


async def list_user_organizations(db: AsyncSession, user_id: UUID) -> list[OrganizationSummary]:
    query = (
        active_memberships(user_id)
        .where(Organization.status == "active", Tenant.status.in_(("active", "suspended")))
        .order_by(Tenant.name, Organization.name, Organization.id)
    )
    summaries: list[OrganizationSummary] = []
    for organization, tenant in (await db.execute(query)).all():
        roles, capabilities = await organization_permissions(db, organization.id, user_id)
        summaries.append(
            OrganizationSummary.model_validate(
                {
                    "id": organization.id,
                    "name": organization.name,
                    "slug": organization.slug,
                    "tenant_id": tenant.id,
                    "tenant_name": tenant.name,
                    "tenant_status": tenant.status,
                    "is_default": organization.is_default,
                    "roles": roles,
                    "capabilities": capabilities,
                }
            )
        )
    return summaries


async def select_active_organization(
    db: AsyncSession, actor: OrganizationActor, org_id: UUID
) -> OrgContext:
    """A missing organization and a foreign one have the same external response."""
    membership = (
        await db.execute(active_memberships(actor.id).where(Organization.id == org_id))
    ).first()
    if membership is None:
        raise AppError(code="not_found", message="조직을 찾을 수 없습니다", status=404)
    context = await resolve_org_context(db, actor, cookie=str(org_id))
    user = await db.get(User, actor.id)
    if user is None:
        raise AppError(code="not_authenticated", message="인증이 필요합니다", status=401)
    user.last_active_org_id = org_id
    await db.commit()
    return context
