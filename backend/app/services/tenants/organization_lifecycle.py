"""Company organization archive and restore (parent Task 10, policy A).

Every default organization — active or a legacy archived default — is never
archivable: after authentication, company authority and the Tenant ->
Organization locks, a locked ``is_default`` row returns
``409 default_organization_archive_forbidden`` before any version, name or
last-organization check, with no state or audit change. Nondefault archives
apply stale ``If-Match``, then active state, then the exact confirmation name,
then the last-nonarchived-organization rule; only then does the accepted
branch run the I22 enumeration/terminalization cascade in the same
transaction. Restore applies stale ``If-Match``, then archived state, then the
30-day window, and clears the archive stamp only (``organization_restore``);
it never changes ``is_default`` and never re-enables or replays work.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.exceptions import AppError
from app.models.organization import Organization
from app.schemas.tenant_organization_lifecycle import TenantOrganizationArchive
from app.schemas.tenant_organizations import TenantOrganizationSummary
from app.services import audit_service
from app.services.orgs.versioning import bump_version, require_current_version
from app.services.tenants import organization_archive_terminalizer as terminalizer
from app.services.tenants.organization_restore import restore_archive_metadata
from app.services.tenants.organizations import TenantOrganizationMutation, _projections
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)


async def _locked_target(
    mutation: TenantOrganizationMutation, context: TenantAdminContext, org_id: UUID
) -> Organization:
    db = mutation.db
    # HTML plan API table: archive/restore require can_create_org (company admin).
    await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, "can_create_org")
    )
    locked = await lock_tenant_org_write(db, context.tenant.id, (org_id,))
    return locked.organizations[0]


async def _other_nonarchived_count(mutation: TenantOrganizationMutation, org: Organization) -> int:
    count = await mutation.db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(
            Organization.tenant_id == org.tenant_id,
            Organization.id != org.id,
            Organization.status != "archived",
        )
    )
    return int(count or 0)


async def _record(
    mutation: TenantOrganizationMutation,
    context: TenantAdminContext,
    organization: Organization,
    action: str,
) -> None:
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=mutation.actor.email,
        owner_user_id=context.actor.id,
        action=action,
        target_type="organization",
        target_id=organization.id,
        target_name_snapshot=organization.name,
        outcome="success",
        request=mutation.request,
        metadata={"tenant_id": str(context.tenant.id), "is_default": organization.is_default},
    )


async def _summary(
    mutation: TenantOrganizationMutation, organization: Organization
) -> TenantOrganizationSummary:
    projection = (await _projections(mutation.db, [organization]))[0]
    return TenantOrganizationSummary.from_projection(projection)


async def archive_organization(
    mutation: TenantOrganizationMutation,
    context: TenantAdminContext,
    org_id: UUID,
    payload: TenantOrganizationArchive,
) -> TenantOrganizationSummary:
    db = mutation.db
    paused_trigger_ids: tuple[UUID, ...] = ()
    try:
        organization = await _locked_target(mutation, context, org_id)
        if organization.is_default:
            raise authorization_error("default_organization_archive_forbidden")
        require_current_version(organization, mutation.expected_version)
        if organization.status != "active":
            # Archived or suspended rows are not archivable (restore must never
            # loosen a suspension), and the admin already passed authorization.
            raise authorization_error("organization_unavailable")
        if payload.confirm_name != organization.name:
            raise authorization_error("organization_archive_name_mismatch")
        if await _other_nonarchived_count(mutation, organization) == 0:
            raise authorization_error("last_organization")
        archived_at = datetime.now(UTC)
        cascade = await terminalizer.cascade_organization_archive(db, organization, archived_at)
        paused_trigger_ids = cascade.paused_trigger_ids
        organization.status = "archived"
        organization.archived_at = archived_at
        bump_version(organization)
        await _record(mutation, context, organization, audit_actions.ORG_ARCHIVE)
        await db.commit()
    except (AppError, SQLAlchemyError, ExecutionProofError):
        await db.rollback()
        raise
    _unschedule(paused_trigger_ids)
    return await _summary(mutation, organization)


def _unschedule(trigger_ids: tuple[UUID, ...]) -> None:
    """Post-commit: drop scheduler jobs so a later restore cannot fire paused triggers."""
    if not trigger_ids:
        return
    from app import scheduler

    for trigger_id in trigger_ids:
        scheduler.remove_trigger_job(trigger_id)


async def restore_organization(
    mutation: TenantOrganizationMutation,
    context: TenantAdminContext,
    org_id: UUID,
) -> TenantOrganizationSummary:
    db = mutation.db
    try:
        organization = await _locked_target(mutation, context, org_id)
        require_current_version(organization, mutation.expected_version)
        restore_archive_metadata(organization)
        bump_version(organization)
        await _record(mutation, context, organization, audit_actions.ORG_RESTORE)
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
    return await _summary(mutation, organization)
