"""Membership lookups the usage limits need (plan 8.7; H-06).

Kept in the authorization package with the other ownership/membership predicates:
company admins (warning recipients), one organization member row (the per-organization
user limit), whether an email already belongs to a company member (invitations), and
whether a run's caller may see a scope's amounts (``can_view_amounts``: their own user
limit always; company scope with ``can_view_tenant_usage``; organization and agent
scopes with ``can_view_usage`` — company admin/auditor or org admin/auditor).
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_permissions import organization_permissions
from app.models.organization import OrganizationMember, TenantMember, TenantRoleGrant
from app.models.user import User

_OVERSIGHT = frozenset({"admin", "auditor"})


async def company_admins(db: AsyncSession, tenant_id: UUID) -> set[UUID]:
    query = (
        select(TenantRoleGrant.user_id)
        .join(
            TenantMember,
            (TenantMember.tenant_id == TenantRoleGrant.tenant_id)
            & (TenantMember.user_id == TenantRoleGrant.user_id),
        )
        .where(
            TenantRoleGrant.tenant_id == tenant_id,
            TenantRoleGrant.role == "admin",
            TenantMember.status == "active",
        )
    )
    return set(await db.scalars(query))


async def org_member(db: AsyncSession, org_id: UUID, user_id: UUID) -> OrganizationMember | None:
    return await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id, OrganizationMember.user_id == user_id
        )
    )


async def is_company_member_email(db: AsyncSession, tenant_id: UUID, email: str) -> bool:
    found = await db.scalar(
        select(TenantMember.user_id)
        .join(User, User.id == TenantMember.user_id)
        .where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.status != "removed",
            func.lower(User.email) == email.casefold(),
        )
    )
    return found is not None


async def can_view_amounts(
    db: AsyncSession, scope: str, *, tenant_id: UUID, org_id: UUID, user_id: UUID
) -> bool:
    if scope == "user":
        return True
    company = set(
        await db.scalars(
            select(TenantRoleGrant.role).where(
                TenantRoleGrant.tenant_id == tenant_id, TenantRoleGrant.user_id == user_id
            )
        )
    )
    if company & _OVERSIGHT or scope == "tenant":
        return bool(company & _OVERSIGHT)
    roles, _ = await organization_permissions(db, org_id, user_id)
    return not _OVERSIGHT.isdisjoint(roles)


__all__ = ["can_view_amounts", "company_admins", "is_company_member_email", "org_member"]
