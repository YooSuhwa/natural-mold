"""Shared parent-first locks for membership-derived writes."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import ErrorCode, authorization_error
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
)
from app.models.organization_group import GroupMember, OrgCapabilityGrant

CURRENT_MEMBERSHIP_WRITERS: Final = (
    "backend/app/services/orgs/members.py",
    "backend/app/services/orgs/roles.py",
    "backend/app/services/orgs/invitation_acceptance.py",
    "backend/app/services/orgs/invitation_membership.py",
    "backend/app/services/registration_policy.py",
    "backend/app/services/registration_membership.py",
)
FUTURE_MEMBERSHIP_WRITER_CONTRACTS: Final = (
    "backend/app/services/tenants/organizations.py",
    "backend/app/services/tenants/members.py",
    "backend/app/services/tenants/roles.py",
    "backend/app/services/tenants/invitations.py",
)


@dataclass(frozen=True, slots=True)
class TenantOrgWriteLock:
    tenant: Tenant
    organizations: tuple[Organization, ...]


@dataclass(frozen=True, slots=True)
class TenantOrgWriteTarget:
    tenant_id: UUID
    organization_ids: Collection[UUID] | None
    missing_code: ErrorCode = "not_found"


@dataclass(frozen=True, slots=True)
class TenantOrgWriteSet:
    tenants: tuple[Tenant, ...]
    organizations: tuple[Organization, ...]


async def lock_tenant_org_writes(
    db: AsyncSession, targets: Collection[TenantOrgWriteTarget]
) -> TenantOrgWriteSet:
    """Lock every Tenant parent before any sorted Organization child."""
    missing_by_tenant: dict[UUID, ErrorCode] = {}
    for target in targets:
        missing_by_tenant.setdefault(target.tenant_id, target.missing_code)
    tenants: list[Tenant] = []
    for tenant_id in sorted(missing_by_tenant, key=lambda value: value.bytes):
        tenant = await db.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tenant is None:
            raise authorization_error(missing_by_tenant[tenant_id])
        tenants.append(tenant)
    organization_targets: dict[UUID, tuple[UUID, ErrorCode]] = {}
    for target in targets:
        organization_ids = target.organization_ids
        if organization_ids is None:
            organization_ids = tuple(
                (
                    await db.scalars(
                        select(Organization.id).where(Organization.tenant_id == target.tenant_id)
                    )
                ).all()
            )
        for organization_id in organization_ids:
            organization_targets.setdefault(
                organization_id,
                (target.tenant_id, target.missing_code),
            )
    organizations: list[Organization] = []
    for organization_id in sorted(organization_targets, key=lambda value: value.bytes):
        tenant_id, missing_code = organization_targets[organization_id]
        organization = await db.scalar(
            select(Organization)
            .where(
                Organization.id == organization_id,
                Organization.tenant_id == tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if organization is None:
            raise authorization_error(missing_code)
        organizations.append(organization)
    return TenantOrgWriteSet(tuple(tenants), tuple(organizations))


async def lock_tenant_org_write(
    db: AsyncSession,
    tenant_id: UUID,
    organization_ids: Collection[UUID] | None,
    missing_code: ErrorCode = "not_found",
) -> TenantOrgWriteLock:
    """Lock Tenant, then preexisting organizations in UUID-byte order."""
    locked = await lock_tenant_org_writes(
        db,
        (TenantOrgWriteTarget(tenant_id, organization_ids, missing_code),),
    )
    return TenantOrgWriteLock(locked.tenants[0], locked.organizations)


async def lock_member_rows(
    db: AsyncSession, tenant_id: UUID, organization_id: UUID, user_id: UUID
) -> None:
    """Lock one member's child rows in stable table/key order."""
    await db.scalar(
        select(TenantMember)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await db.scalar(
        select(OrganizationMember)
        .where(
            OrganizationMember.org_id == organization_id,
            OrganizationMember.tenant_id == tenant_id,
            OrganizationMember.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await db.scalars(
        select(OrganizationRoleGrant)
        .where(
            OrganizationRoleGrant.org_id == organization_id,
            OrganizationRoleGrant.tenant_id == tenant_id,
            OrganizationRoleGrant.principal_type == "user",
            OrganizationRoleGrant.principal_id == user_id,
        )
        .order_by(OrganizationRoleGrant.id)
        .with_for_update()
    )
    await db.scalars(
        select(GroupMember)
        .where(
            GroupMember.org_id == organization_id,
            GroupMember.tenant_id == tenant_id,
            GroupMember.user_id == user_id,
        )
        .order_by(GroupMember.group_id)
        .with_for_update()
    )
    await db.scalars(
        select(OrgCapabilityGrant)
        .where(
            OrgCapabilityGrant.org_id == organization_id,
            OrgCapabilityGrant.tenant_id == tenant_id,
            OrgCapabilityGrant.principal_type == "user",
            OrgCapabilityGrant.principal_id == user_id,
        )
        .order_by(OrgCapabilityGrant.id)
        .with_for_update()
    )
