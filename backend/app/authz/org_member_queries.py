"""Scoped roster/name projections and company-approved member targets."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlContext
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User


def member_query(
    context: OrgControlContext,
) -> Select[tuple[OrganizationMember, str, str | None, str]]:
    return (
        select(OrganizationMember, User.name, User.display_name, User.email)
        .join(
            User,
            User.id == OrganizationMember.user_id,
        )
        .where(
            OrganizationMember.org_id == context.organization.id,
            OrganizationMember.tenant_id == context.organization.tenant_id,
        )
    )


def member_for_user(
    context: OrgControlContext, user_id: UUID
) -> Select[tuple[OrganizationMember, str, str | None, str]]:
    return member_query(context).where(OrganizationMember.user_id == user_id)


async def company_member_target(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    found = await db.scalar(
        select(User.id)
        .join(TenantMember, TenantMember.user_id == User.id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            TenantMember.tenant_id == context.organization.tenant_id,
            TenantMember.status == "active",
        )
    )
    if found is None:
        raise authorization_error("not_found")
