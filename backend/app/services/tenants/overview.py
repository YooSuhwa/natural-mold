"""Company overview reads and versioned settings mutations."""

from dataclasses import dataclass
from typing import assert_never

from fastapi import Request
from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.models.organization import (
    Organization,
    OrganizationMember,
    TenantMember,
    TenantRoleGrant,
)
from app.schemas.tenant_organizations import TenantOrganizationAuditorSummary
from app.schemas.tenant_overview import (
    TenantAdminOverview,
    TenantAuditorOverview,
    TenantOverview,
    TenantOverviewPatch,
)
from app.services import audit_service
from app.services.tenants.versioning import bump_timestamp, require_current_version
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

TenantOverviewResult = TenantAdminOverview | TenantAuditorOverview | TenantOverview


@dataclass(frozen=True, slots=True)
class TenantOverviewMutation:
    db: AsyncSession
    request: Request
    expected_version: str | None


@dataclass(frozen=True, slots=True)
class TenantOverviewAggregates:
    active_member_count: int
    organizations: tuple[TenantOrganizationAuditorSummary, ...]


async def get_tenant_overview(
    db: AsyncSession, context: TenantAdminContext
) -> TenantOverviewResult:
    """Return only the projection authorized by the actor's company role."""
    tenant = context.tenant
    base = TenantOverview.from_tenant(tenant, access=context.access)
    match context.access:
        case "member":
            return base
        case "auditor":
            aggregates = await _overview_aggregates(db, context)
            return TenantAuditorOverview(
                id=base.id,
                name=base.name,
                slug=base.slug,
                status=base.status,
                updated_at=base.updated_at,
                access=base.access,
                active_member_count=aggregates.active_member_count,
                organization_count=len(aggregates.organizations),
                organizations=aggregates.organizations,
            )
        case "admin":
            return await _admin_overview(db, context)
        case unreachable:
            assert_never(unreachable)


async def _overview_aggregates(
    db: AsyncSession, context: TenantAdminContext
) -> TenantOverviewAggregates:
    tenant = context.tenant
    active_member_count = await db.scalar(
        select(func.count())
        .select_from(TenantMember)
        .where(TenantMember.tenant_id == tenant.id, TenantMember.status == "active")
    )
    rows = (
        await db.execute(
            select(Organization, func.count(OrganizationMember.user_id))
            .outerjoin(
                OrganizationMember,
                and_(
                    OrganizationMember.org_id == Organization.id,
                    OrganizationMember.tenant_id == tenant.id,
                    OrganizationMember.status == "active",
                ),
            )
            .where(Organization.tenant_id == tenant.id, Organization.status == "active")
            .group_by(Organization.id)
            .order_by(Organization.name, Organization.id)
        )
    ).all()
    return TenantOverviewAggregates(
        active_member_count=active_member_count or 0,
        organizations=tuple(
            TenantOrganizationAuditorSummary(
                id=organization.id,
                name=organization.name,
                member_count=member_count,
            )
            for organization, member_count in rows
        ),
    )


async def _admin_overview(db: AsyncSession, context: TenantAdminContext) -> TenantAdminOverview:
    tenant = context.tenant
    base = TenantOverview.from_tenant(tenant, access="admin")
    aggregates = await _overview_aggregates(db, context)
    active_admin_count = await db.scalar(
        select(func.count())
        .select_from(TenantRoleGrant)
        .join(
            TenantMember,
            and_(
                TenantMember.tenant_id == TenantRoleGrant.tenant_id,
                TenantMember.user_id == TenantRoleGrant.user_id,
            ),
        )
        .where(
            TenantRoleGrant.tenant_id == tenant.id,
            TenantRoleGrant.role == "admin",
            TenantMember.status == "active",
        )
    )
    return TenantAdminOverview(
        id=base.id,
        name=base.name,
        slug=base.slug,
        status=base.status,
        updated_at=base.updated_at,
        access=base.access,
        active_member_count=aggregates.active_member_count,
        organization_count=len(aggregates.organizations),
        organizations=aggregates.organizations,
        active_admin_count=active_admin_count or 0,
    )


async def update_tenant_overview(
    mutation: TenantOverviewMutation,
    context: TenantAdminContext,
    payload: TenantOverviewPatch,
) -> TenantAdminOverview:
    """Apply one allowlisted company change with its audit in one transaction."""
    fresh = await load_tenant_admin_context(
        mutation.db,
        context.actor,
        TenantAdminTarget(context.tenant.id, permission="can_manage_tenant"),
    )
    tenant = fresh.tenant
    require_current_version(tenant.updated_at, mutation.expected_version)
    if payload.name == tenant.name:
        return await _admin_overview(mutation.db, fresh)

    try:
        tenant.name = payload.name
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        await audit_service.record_event(
            mutation.db,
            actor_type="user",
            actor_user_id=fresh.actor.id,
            owner_user_id=fresh.actor.id,
            action=audit_actions.TENANT_UPDATE,
            target_type="tenant",
            target_id=tenant.id,
            target_name_snapshot=tenant.name,
            outcome="success",
            request=mutation.request,
            metadata={"tenant_id": str(tenant.id), "changed_fields": ["name"]},
        )
        await mutation.db.commit()
    except SQLAlchemyError:
        await mutation.db.rollback()
        raise

    return await _admin_overview(mutation.db, fresh)
