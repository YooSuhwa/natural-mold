"""Group roster projections select only names/emails and bind all parent scopes."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.sql import Select

from app.authz.org_control import OrgControlContext
from app.models.organization import OrganizationMember, TenantMember
from app.models.organization_group import GroupMember
from app.models.user import User


def group_member_query(
    context: OrgControlContext,
) -> Select[tuple[GroupMember, str, str | None, str]]:
    return (
        select(GroupMember, User.name, User.display_name, User.email)
        .join(
            User,
            User.id == GroupMember.user_id,
        )
        .join(
            OrganizationMember,
            (
                (OrganizationMember.user_id == GroupMember.user_id)
                & (OrganizationMember.org_id == GroupMember.org_id)
                & (OrganizationMember.tenant_id == GroupMember.tenant_id)
            ),
        )
        .join(
            TenantMember,
            (
                (TenantMember.user_id == GroupMember.user_id)
                & (TenantMember.tenant_id == GroupMember.tenant_id)
            ),
        )
        .where(
            GroupMember.org_id == context.organization.id,
            GroupMember.tenant_id == context.organization.tenant_id,
            OrganizationMember.status == "active",
            TenantMember.status == "active",
            User.is_active.is_(True),
        )
    )


def group_user_query(
    context: OrgControlContext,
    group_id: UUID,
    user_id: UUID,
) -> Select[tuple[GroupMember, str, str | None, str]]:
    return group_member_query(context).where(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
    )
