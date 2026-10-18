"""Tenant-first write preflight shared by company member and role mutations."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlActor
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.services.tenants.versioning import require_current_version
from app.tenancy.context import ensure_tenant_active


@dataclass(frozen=True, slots=True)
class TenantMemberWrite:
    db: AsyncSession
    tenant_id: UUID
    actor: OrgControlActor
    request: Request
    expected_version: str | None


@dataclass(frozen=True, slots=True)
class TenantMemberWriteContext:
    tenant: Tenant
    members: tuple[TenantMember, ...]
    roles: tuple[TenantRoleGrant, ...]

    def member(self, user_id: UUID) -> TenantMember | None:
        return next((row for row in self.members if row.user_id == user_id), None)

    def user_roles(self, user_id: UUID) -> tuple[TenantRoleGrant, ...]:
        return tuple(row for row in self.roles if row.user_id == user_id)

    def active_admin_ids(self) -> set[UUID]:
        active = {row.user_id for row in self.members if row.status == "active"}
        return {row.user_id for row in self.roles if row.role == "admin" and row.user_id in active}


async def lock_tenant_member_write(
    command: TenantMemberWrite, *, lock_children: bool, lock_organizations: bool = False
) -> TenantMemberWriteContext:
    """Lock Tenant first, authorize the caller, then lock children; DELETE defers them to exit.

    ``lock_organizations`` also locks every organization (tenant first, then UUID-byte
    order) before the children: company role/status changes record a revocation marker in
    each organization (8.6, ``membership_sync``)."""
    db = command.db
    with db.no_autoflush:
        orgs = None if lock_organizations else ()
        locked = await lock_tenant_org_write(db, command.tenant_id, orgs)
        members, roles = await _child_rows(command, actor_only=True, lock=False)
        _authorize(locked.tenant, members, roles, command.actor.id)
        if lock_children:
            members, roles = await _child_rows(command, actor_only=False, lock=True)
            _authorize(locked.tenant, members, roles, command.actor.id)
        require_current_version(locked.tenant.updated_at, command.expected_version)
        return TenantMemberWriteContext(locked.tenant, members, roles)


async def _child_rows(
    command: TenantMemberWrite, *, actor_only: bool, lock: bool
) -> tuple[tuple[TenantMember, ...], tuple[TenantRoleGrant, ...]]:
    member_query = select(TenantMember).where(TenantMember.tenant_id == command.tenant_id)
    role_query = select(TenantRoleGrant).where(TenantRoleGrant.tenant_id == command.tenant_id)
    if actor_only:
        member_query = member_query.where(TenantMember.user_id == command.actor.id)
        role_query = role_query.where(TenantRoleGrant.user_id == command.actor.id)
    if lock:
        member_query = member_query.order_by(TenantMember.user_id).with_for_update()
        role_query = role_query.order_by(
            TenantRoleGrant.user_id, TenantRoleGrant.id
        ).with_for_update()
    db = command.db
    members = tuple(
        (await db.scalars(member_query.execution_options(populate_existing=True))).all()
    )
    roles = tuple((await db.scalars(role_query.execution_options(populate_existing=True))).all())
    return members, roles


def _authorize(
    tenant: Tenant,
    members: tuple[TenantMember, ...],
    roles: tuple[TenantRoleGrant, ...],
    actor_id: UUID,
) -> None:
    """Non-members get the enumeration-safe 404 before any company status is revealed."""
    actor_member = next((row for row in members if row.user_id == actor_id), None)
    if actor_member is None or actor_member.status != "active":
        raise authorization_error("not_found")
    ensure_tenant_active(tenant)
    if not any(row.user_id == actor_id and row.role == "admin" for row in roles):
        raise authorization_error("permission_required")
