"""Company-scoped organization directory, creation and rename."""

from dataclasses import dataclass
from typing import assert_never
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.catalog_fanout import sync_organization
from app.authz.error_codes import authorization_error
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.service import AuthzService
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, OrganizationRoleGrant
from app.schemas import tenant_organizations as schemas
from app.services import audit_service
from app.services.orgs.versioning import bump_version, require_current_version
from app.services.tenants import pagination
from app.services.tenants.identifiers import organization_slug_base, organization_slug_for_ordinal
from app.services.tenants.organization_administrators import assign_initial_administrator
from app.tenancy.admin_context import TenantAdminContext as AdminContext
from app.tenancy.admin_context import TenantAdminTarget as AdminTarget
from app.tenancy.admin_context import load_tenant_admin_context as load_context

TenantOrganizationPage = schemas.TenantOrganizationAdminPage | schemas.TenantOrganizationAuditorPage


@dataclass(frozen=True, slots=True)
class TenantOrganizationMutation:
    db: AsyncSession
    request: Request
    expected_version: str | None
    actor: CurrentUser


async def list_organizations(
    db: AsyncSession,
    context: AdminContext,
    query: pagination.TenantPageQuery,
) -> TenantOrganizationPage:
    fresh = await load_context(db, context.actor, AdminTarget(context.tenant.id))
    if fresh.access == "member":
        raise authorization_error("permission_required")
    scope = pagination.TenantCursorScope(
        list_kind="tenant_organizations", tenant_id=fresh.tenant.id, filter_key="all"
    )
    cursor = pagination.decode_cursor(query.cursor, scope)
    statement = select(Organization).where(Organization.tenant_id == fresh.tenant.id)
    if cursor is not None:
        statement = statement.where(
            or_(
                Organization.updated_at > cursor.sort_value,
                and_(
                    Organization.updated_at == cursor.sort_value,
                    Organization.id > cursor.position,
                ),
            )
        )
    rows = list(
        (
            await db.scalars(
                statement.order_by(Organization.updated_at, Organization.id).limit(query.limit + 1)
            )
        ).all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    projections = await _projections(db, selected)
    next_cursor = (
        pagination.encode_cursor(
            pagination.TenantCursor(
                scope=scope, sort_value=selected[-1].updated_at, position=selected[-1].id
            )
        )
        if more
        else None
    )
    match fresh.access:
        case "admin":
            return schemas.TenantOrganizationAdminPage(
                items=tuple(
                    schemas.TenantOrganizationSummary.from_projection(row) for row in projections
                ),
                next_cursor=next_cursor,
                has_more=more,
            )
        case "auditor":
            return schemas.TenantOrganizationAuditorPage(
                items=tuple(
                    schemas.TenantOrganizationAuditorSummary(
                        id=row.organization.id,
                        name=row.organization.name,
                        member_count=row.member_count,
                    )
                    for row in projections
                ),
                next_cursor=next_cursor,
                has_more=more,
            )
        case unreachable:
            assert_never(unreachable)


async def _projections(
    db: AsyncSession, organizations: list[Organization]
) -> tuple[schemas.TenantOrganizationProjection, ...]:
    ids = tuple(row.id for row in organizations)
    count_rows = (
        (
            await db.execute(
                select(OrganizationMember.org_id, func.count())
                .where(OrganizationMember.org_id.in_(ids), OrganizationMember.status == "active")
                .group_by(OrganizationMember.org_id)
            )
        )
        .tuples()
        .all()
    )
    admin_rows = (
        await db.execute(
            select(OrganizationRoleGrant.org_id, OrganizationRoleGrant.principal_id)
            .join(
                OrganizationMember,
                and_(
                    OrganizationMember.org_id == OrganizationRoleGrant.org_id,
                    OrganizationMember.user_id == OrganizationRoleGrant.principal_id,
                    OrganizationMember.status == "active",
                ),
            )
            .where(
                OrganizationRoleGrant.org_id.in_(ids),
                OrganizationRoleGrant.principal_type == "user",
                OrganizationRoleGrant.role == "admin",
            )
            .order_by(OrganizationRoleGrant.org_id, OrganizationRoleGrant.principal_id)
        )
    ).all()
    counts = dict(count_rows)
    administrators: dict[UUID, UUID] = {}
    for org_id, user_id in admin_rows:
        administrators.setdefault(org_id, user_id)
    return tuple(
        schemas.TenantOrganizationProjection(row, counts.get(row.id, 0), administrators.get(row.id))
        for row in organizations
    )


async def create_organization(
    mutation: TenantOrganizationMutation,
    context: AdminContext,
    payload: schemas.TenantOrganizationCreate,
    authz: AuthzService | None = None,
) -> schemas.TenantOrganizationCreateResult:
    db = mutation.db
    fanout = LifecycleChange()
    try:
        with db.no_autoflush:
            fresh = await load_context(
                db, context.actor, AdminTarget(context.tenant.id, "can_create_org")
            )
            base = organization_slug_base(payload.name)
            slugs = set(
                await db.scalars(
                    select(Organization.slug).where(Organization.tenant_id == fresh.tenant.id)
                )
            )
            ordinal = 1
            while organization_slug_for_ordinal(base, ordinal) in slugs:
                ordinal += 1
            organization = Organization(
                tenant_id=fresh.tenant.id,
                name=payload.name,
                slug=organization_slug_for_ordinal(base, ordinal),
                settings={"default_member_role": payload.default_member_role},
                created_by=fresh.actor.id,
            )
            db.add(organization)
            await db.flush()
            administrator = await assign_initial_administrator(
                db, mutation.request, organization, mutation.actor, payload.administrator
            )
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=fresh.actor.id,
            actor_email_snapshot=mutation.actor.email,
            owner_user_id=fresh.actor.id,
            action=audit_actions.ORG_CREATE,
            target_type="organization",
            target_id=organization.id,
            target_name_snapshot=organization.name,
            outcome="success",
            request=mutation.request,
            metadata={"tenant_id": str(fresh.tenant.id), "slug": organization.slug},
        )
        if authz is not None:  # E-03: allowed catalog items fan out to the new organization
            fanout = await sync_organization(db, organization, fresh.actor.id, mode=authz.mode)
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
    if authz is not None:
        await apply_lifecycle(fanout, authz)
    projection = (await _projections(db, [organization]))[0]
    return schemas.TenantOrganizationCreateResult(
        organization=schemas.TenantOrganizationSummary.from_projection(projection),
        administrator=administrator,
    )


async def rename_organization(
    mutation: TenantOrganizationMutation,
    context: AdminContext,
    org_id: UUID,
    payload: schemas.TenantOrganizationRename,
) -> schemas.TenantOrganizationSummary:
    db = mutation.db
    try:
        await load_context(db, context.actor, AdminTarget(context.tenant.id, "can_manage_tenant"))
        locked = await lock_tenant_org_write(db, context.tenant.id, (org_id,))
        organization = locked.organizations[0]
        if organization.status != "active":
            raise authorization_error("organization_unavailable")
        require_current_version(organization, mutation.expected_version)
        if organization.name != payload.name:
            organization.name = payload.name
            bump_version(organization)
            await audit_service.record_event(
                db,
                actor_type="user",
                actor_user_id=context.actor.id,
                actor_email_snapshot=mutation.actor.email,
                owner_user_id=context.actor.id,
                action=audit_actions.ORG_UPDATE,
                target_type="organization",
                target_id=organization.id,
                target_name_snapshot=organization.name,
                outcome="success",
                request=mutation.request,
                metadata={"tenant_id": str(context.tenant.id), "changed_fields": ["name"]},
            )
            await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
    return schemas.TenantOrganizationSummary.from_projection(
        (await _projections(db, [organization]))[0]
    )
