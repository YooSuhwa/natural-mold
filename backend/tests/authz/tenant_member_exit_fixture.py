"""Shared fixture support for ordinary company-exit tests."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.models.user import User


@dataclass(frozen=True, slots=True)
class CompanyExitFixture:
    tenant: Tenant
    actor: CurrentUser
    target: User
    organizations: tuple[Organization, ...]


@dataclass(frozen=True, slots=True)
class InjectedLateFailure(Exception):
    organization_id: UUID

    def __str__(self) -> str:
        return f"injected failure for {self.organization_id}"


async def company_exit_fixture(db: AsyncSession) -> CompanyExitFixture:
    actor = User(email="company-admin@example.com", name="Company admin")
    target = User(email="company-member@example.com", name="Company member")
    tenant = Tenant(name="Exit company", slug="exit-company", secrets_namespace="exit-company")
    db.add_all([actor, target, tenant])
    await db.flush()
    organizations = tuple(
        Organization(tenant_id=tenant.id, name=f"Exit {index}", slug=f"exit-{index}")
        for index in range(3)
    )
    db.add_all(organizations)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=actor.id,
                role="admin",
                granted_by=actor.id,
            ),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=target.id,
                role="auditor",
                granted_by=actor.id,
            ),
        ]
    )
    for organization in organizations:
        member = OrganizationMember(
            org_id=organization.id,
            tenant_id=tenant.id,
            user_id=target.id,
        )
        group = Group(
            org_id=organization.id,
            tenant_id=tenant.id,
            name=f"Group {organization.slug}",
        )
        db.add_all([member, group])
        await db.flush()
        db.add_all(
            [
                OrganizationRoleGrant(
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=target.id,
                    role="builder",
                    granted_by=actor.id,
                ),
                GroupMember(
                    group_id=group.id,
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    user_id=target.id,
                    added_by=actor.id,
                ),
                OrgCapabilityGrant(
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=target.id,
                    capability="cap_create_agent",
                    granted_by=actor.id,
                ),
            ]
        )
    await db.commit()
    return CompanyExitFixture(
        tenant,
        CurrentUser(
            id=actor.id,
            email=actor.email,
            name=actor.name,
            is_super_user=actor.is_super_user,
        ),
        target,
        organizations,
    )
