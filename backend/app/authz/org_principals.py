"""A capability/role principal must belong to the target organization boundary."""

from dataclasses import dataclass
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_administrators import active_org_users
from app.authz.org_control import OrgControlContext
from app.authz.org_directory import scoped_group
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.schemas.org_assignments import (
    GroupPrincipal,
    OrganizationPrincipal,
    Principal,
    UserPrincipal,
)


@dataclass(frozen=True, slots=True)
class PrincipalTarget:
    principal: Principal
    auditor_only: bool = False


async def validate_principal(
    db: AsyncSession, context: OrgControlContext, target: PrincipalTarget
) -> None:
    match target.principal:
        case UserPrincipal(id=user_id):
            query = active_org_users(context.organization).where(
                OrganizationMember.user_id == user_id
            )
            if target.auditor_only:
                query = (
                    select(User.id)
                    .join(TenantMember, TenantMember.user_id == User.id)
                    .where(
                        TenantMember.tenant_id == context.organization.tenant_id,
                        TenantMember.status == "active",
                        User.id == user_id,
                        User.is_active.is_(True),
                    )
                )
            if await db.scalar(query) is None:
                raise authorization_error("not_found")
        case GroupPrincipal(id=group_id):
            await scoped_group(db, context, group_id)
        case OrganizationPrincipal(id=org_id):
            if org_id != context.organization.id:
                raise authorization_error("not_found")
        case unknown:
            assert_never(unknown)
