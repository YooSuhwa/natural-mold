"""Atomic company-wide membership exit orchestration."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.membership_projection import UserSubject
from app.authz.membership_sync import MembershipSync
from app.authz.org_control import OrgControlActor, OrgControlContext
from app.authz.org_member_exit import MemberExitPlan, apply_member_exit, preflight_member_exit
from app.authz.session_revocation import revoke_sessions_without_company
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.models.organization import OrganizationMember, TenantMember, TenantRoleGrant
from app.models.user import User
from app.services import audit_service
from app.services.orgs.versioning import bump_version
from app.tenancy.context import ensure_tenant_active


@dataclass(frozen=True, slots=True)
class TenantMemberExitRequest:
    db: AsyncSession
    tenant_id: UUID
    user_id: UUID
    actor: OrgControlActor
    request: Request | None = None


@dataclass(frozen=True, slots=True)
class TenantMemberExitResult:
    organization_ids: tuple[UUID, ...]


async def exit_tenant_member(command: TenantMemberExitRequest) -> TenantMemberExitResult:
    """Remove one member from every organization in one caller transaction."""
    db = command.db
    with db.no_autoflush:
        locked = await lock_tenant_org_write(db, command.tenant_id, None)
        relevant_members = list(
            (
                await db.scalars(
                    select(TenantMember)
                    .where(TenantMember.tenant_id == command.tenant_id)
                    .order_by(TenantMember.user_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        members_by_user = {member.user_id: member for member in relevant_members}
        actor_member = members_by_user.get(command.actor.id)
        if actor_member is None or actor_member.status != "active":
            raise authorization_error("not_found")
        ensure_tenant_active(locked.tenant)
        tenant_roles = list(
            (
                await db.scalars(
                    select(TenantRoleGrant)
                    .where(TenantRoleGrant.tenant_id == command.tenant_id)
                    .order_by(TenantRoleGrant.user_id, TenantRoleGrant.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        actor_is_admin = any(
            role.user_id == command.actor.id and role.role == "admin" for role in tenant_roles
        )
        if not actor_is_admin:
            raise authorization_error("permission_required")
        target_member = members_by_user.get(command.user_id)
        if target_member is None or target_member.status == "removed":
            raise authorization_error("not_found")
        active_admin_ids = {
            role.user_id
            for role in tenant_roles
            if role.role == "admin"
            and (member := members_by_user.get(role.user_id)) is not None
            and member.status == "active"
        }
        if command.user_id in active_admin_ids and len(active_admin_ids) == 1:
            raise authorization_error("last_tenant_admin")
        target_roles = tuple(role for role in tenant_roles if role.user_id == command.user_id)
        memberships = list(
            (
                await db.scalars(
                    select(OrganizationMember)
                    .where(
                        OrganizationMember.tenant_id == command.tenant_id,
                        OrganizationMember.user_id == command.user_id,
                    )
                    .order_by(OrganizationMember.org_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        members_by_org = {member.org_id: member for member in memberships}
        plans: list[MemberExitPlan] = []
        for organization in locked.organizations:
            member = members_by_org.get(organization.id)
            if member is None or member.status == "removed":
                continue
            context = OrgControlContext(organization, command.actor, ("can_manage_members",))
            plans.append(await preflight_member_exit(db, context, command.user_id))
        organization_ids = tuple(plan.context.organization.id for plan in plans)
        user = await db.scalar(
            select(User)
            .where(User.id == command.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subject = UserSubject(command.tenant_id, command.user_id)
        sync = await MembershipSync.capture(db, command.tenant_id, [subject])
    async with db.begin_nested():
        for plan in plans:
            await apply_member_exit(db, plan)
            bump_version(plan.context.organization)
            await record_org_exit(command, plan)
        for role in target_roles:
            await db.delete(role)
        target_member.status = "removed"
        if user is not None and user.last_active_org_id in {
            organization.id for organization in locked.organizations
        }:
            user.last_active_org_id = None
        await record_tenant_exit(command)
        await db.flush()
        await revoke_sessions_without_company(db, command.user_id)
    await sync.queue(db)  # every organization's membership marker + the tenant tuples
    return TenantMemberExitResult(organization_ids)


async def record_org_exit(command: TenantMemberExitRequest, plan: MemberExitPlan) -> None:
    await audit_service.record_event(
        command.db,
        actor_type="user",
        actor_user_id=command.actor.id,
        actor_email_snapshot=command.actor.email,
        owner_user_id=command.actor.id,
        action=audit_actions.ORG_MEMBER_REMOVE,
        target_type="organization_member",
        target_id=command.user_id,
        outcome="success",
        request=command.request,
        metadata={
            "org_id": str(plan.context.organization.id),
            "tenant_id": str(command.tenant_id),
            "status": "removed",
            "authz_epoch": plan.context.organization.authz_epoch,
            "source": "tenant_member_exit",
        },
    )


async def record_tenant_exit(command: TenantMemberExitRequest) -> None:
    await audit_service.record_event(
        command.db,
        actor_type="user",
        actor_user_id=command.actor.id,
        actor_email_snapshot=command.actor.email,
        owner_user_id=command.actor.id,
        action=audit_actions.TENANT_MEMBER_REMOVE,
        target_type="tenant_member",
        target_id=command.user_id,
        outcome="success",
        request=command.request,
        metadata={"tenant_id": str(command.tenant_id), "status": "removed"},
    )
