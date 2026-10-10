"""Membership boundary used before any organization-scoped resource lookup."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User


class OrganizationActor(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def is_super_user(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class OrgContext:
    org_id: UUID
    tenant_id: UUID
    user_id: UUID
    is_super_user: bool = False


def active_memberships(user_id: UUID) -> Select[tuple[Organization, Tenant]]:
    """Both company and organization membership must currently be active."""
    return (
        select(Organization, Tenant)
        .join(Tenant, Organization.tenant_id == Tenant.id)
        .join(OrganizationMember, OrganizationMember.org_id == Organization.id)
        .join(TenantMember, TenantMember.tenant_id == Tenant.id)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.tenant_id == Tenant.id,
            OrganizationMember.status == "active",
            TenantMember.user_id == user_id,
            TenantMember.status == "active",
        )
    )


async def resolve_org_context(
    db: AsyncSession, user: OrganizationActor, *, cookie: str | None
) -> OrgContext:
    """Explicit invalid cookies are denied; stale saved preferences may fall back.

    FastAPI caches this dependency within a request. Membership is read from the
    database on every request so suspension/removal never depends on a process TTL.
    """
    query = active_memberships(user.id)
    if cookie is not None:
        try:
            requested = UUID(cookie)
        except ValueError as error:
            raise AppError(code="forbidden", message="권한이 없습니다", status=403) from error
        membership = (await db.execute(query.where(Organization.id == requested))).first()
        if membership is None:
            raise AppError(code="forbidden", message="권한이 없습니다", status=403)
    else:
        preferred = await db.scalar(select(User.last_active_org_id).where(User.id == user.id))
        membership = None
        if preferred is not None:
            membership = (await db.execute(query.where(Organization.id == preferred))).first()
        if membership is None:
            membership = (
                await db.execute(
                    query.where(Organization.status == "active", Tenant.status == "active")
                    .order_by(Tenant.name, Organization.name, Organization.id)
                    .limit(1)
                )
            ).first()
        if membership is None:
            raise AppError(code="no_organization", message="참여한 조직이 없습니다", status=409)
    organization, tenant = membership
    if tenant.status == "suspended":
        raise AppError(code="tenant_suspended", message="회사가 정지되었습니다", status=403)
    if tenant.status != "active" or organization.status != "active":
        raise AppError(
            code="organization_unavailable", message="사용할 수 없는 조직입니다", status=403
        )
    return OrgContext(
        org_id=organization.id,
        tenant_id=tenant.id,
        user_id=user.id,
        is_super_user=user.is_super_user,
    )
