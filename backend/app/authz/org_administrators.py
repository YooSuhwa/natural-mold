"""Effective admins require active user, company and organization membership."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
)
from app.models.organization_group import Group, GroupMember
from app.models.user import User


def active_org_users(organization: Organization) -> Select[tuple[UUID]]:
    return (
        select(OrganizationMember.user_id)
        .join(
            TenantMember,
            (
                (TenantMember.user_id == OrganizationMember.user_id)
                & (TenantMember.tenant_id == OrganizationMember.tenant_id)
            ),
        )
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.org_id == organization.id,
            OrganizationMember.tenant_id == organization.tenant_id,
            OrganizationMember.status == "active",
            TenantMember.status == "active",
            User.is_active.is_(True),
        )
    )


async def active_administrators(db: AsyncSession, organization: Organization) -> set[UUID]:
    members = active_org_users(organization)
    direct = select(OrganizationRoleGrant.principal_id).where(
        OrganizationRoleGrant.org_id == organization.id,
        OrganizationRoleGrant.tenant_id == organization.tenant_id,
        OrganizationRoleGrant.role == "admin",
        OrganizationRoleGrant.principal_type == "user",
        OrganizationRoleGrant.principal_id.in_(members),
    )
    grouped = (
        select(GroupMember.user_id)
        .join(
            Group,
            (
                (Group.id == GroupMember.group_id)
                & (Group.org_id == GroupMember.org_id)
                & (Group.tenant_id == GroupMember.tenant_id)
            ),
        )
        .join(
            OrganizationRoleGrant,
            (
                (OrganizationRoleGrant.principal_id == Group.id)
                & (OrganizationRoleGrant.org_id == Group.org_id)
                & (OrganizationRoleGrant.tenant_id == Group.tenant_id)
            ),
        )
        .where(
            GroupMember.org_id == organization.id,
            GroupMember.tenant_id == organization.tenant_id,
            OrganizationRoleGrant.role == "admin",
            OrganizationRoleGrant.principal_type == "group",
            GroupMember.user_id.in_(members),
        )
    )
    return set((await db.scalars(direct.union(grouped))).all())
