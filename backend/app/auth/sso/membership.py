"""Company/organization membership from an SSO login (plan 10.10 "가입").

JIT (``jit_enabled``) makes a verified-domain user a company member on first login;
without it only existing (invited) members get in. IdP groups map to organization
memberships, organization roles and Moldy groups through ``group_role_mapping``::

    {"<idp group>": {"org_id": "<uuid>", "role": "member|builder|admin|auditor",
                      "group_id": "<uuid, optional>"}}   # or a list of such objects

Mapping is additive on every login (never revokes); without a matching group a new
member joins the company's default organization with ``default_role``. Suspended or
removed memberships are never re-activated by a login.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.errors import sso_error
from app.authz import audit_actions
from app.authz.membership_capture import LazySync
from app.authz.membership_projection import UserSubject
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
)
from app.models.organization_group import Group, GroupMember
from app.models.organization_identity import SsoConnection
from app.models.user import User
from app.services import audit_service
from app.services.orgs.versioning import bump_version

ROLES: Final = frozenset({"member", "builder", "admin", "auditor"})


@dataclass(frozen=True, slots=True)
class RoleTarget:
    org_id: UUID
    role: str
    group_id: UUID | None = None


def parse_target(raw: JsonValue) -> RoleTarget | None:
    if not isinstance(raw, dict) or raw.get("role", "member") not in ROLES:
        return None
    try:
        org_id = UUID(str(raw.get("org_id")))
        group = raw.get("group_id")
        group_id = UUID(str(group)) if group else None
    except ValueError:
        return None
    return RoleTarget(org_id, str(raw.get("role", "member")), group_id)


def mapping_targets(mapping: dict[str, JsonValue], groups: tuple[str, ...]) -> list[RoleTarget]:
    found: list[RoleTarget] = []
    for group in groups:
        entry = mapping.get(group)
        for raw in entry if isinstance(entry, list) else [entry]:
            target = parse_target(raw)
            if target is not None and target not in found:
                found.append(target)
    return found


@dataclass(frozen=True, slots=True)
class Join:
    db: AsyncSession
    request: Request
    tenant: Tenant
    user: User


async def _audit(join: Join, action: str, target: tuple[str, UUID], org_id: UUID | None) -> None:
    await audit_service.record_event(
        join.db,
        actor_type="user",
        actor_user_id=join.user.id,
        actor_email_snapshot=join.user.email,
        owner_user_id=join.user.id,
        action=action,
        target_type=target[0],
        target_id=target[1],
        outcome="success",
        request=join.request,
        tenant_id=join.tenant.id,
        metadata={
            "source": "sso",
            "tenant_id": str(join.tenant.id),
            "org_id": str(org_id) if org_id else None,
            "principal_id": str(join.user.id),
        },
    )


async def _default_org(db: AsyncSession, tenant_id: UUID) -> Organization | None:
    return await db.scalar(
        select(Organization)
        .where(Organization.tenant_id == tenant_id, Organization.status == "active")
        .order_by(Organization.is_default.desc(), Organization.created_at, Organization.id)
        .limit(1)
    )


async def _apply(join: Join, target: RoleTarget, sync: LazySync) -> bool:
    db, user = join.db, join.user
    org = await db.get(Organization, target.org_id)
    if org is None or org.tenant_id != join.tenant.id or org.status != "active":
        return False
    member = await db.get(OrganizationMember, (org.id, user.id))
    if member is not None and member.status != "active":
        return False
    changed = member is None
    if member is None:
        await sync.before_change(db)
        db.add(OrganizationMember(org_id=org.id, tenant_id=org.tenant_id, user_id=user.id))
        await _audit(join, audit_actions.ORG_MEMBER_ADD, ("organization_member", user.id), org.id)
    if target.role != "member":
        grant = await db.scalar(
            select(OrganizationRoleGrant.id).where(
                OrganizationRoleGrant.org_id == org.id,
                OrganizationRoleGrant.principal_type == "user",
                OrganizationRoleGrant.principal_id == user.id,
                OrganizationRoleGrant.role == target.role,
            )
        )
        if grant is None:
            await sync.before_change(db)
            db.add(
                OrganizationRoleGrant(
                    org_id=org.id,
                    tenant_id=org.tenant_id,
                    role=target.role,
                    principal_type="user",
                    principal_id=user.id,
                )
            )
            await _audit(join, audit_actions.ORG_ROLE_GRANT, ("organization", org.id), org.id)
            changed = True
    if target.group_id is not None:
        group = await db.get(Group, target.group_id)
        if (
            group is not None
            and group.org_id == org.id
            and await db.get(GroupMember, (group.id, user.id)) is None
        ):
            await sync.before_change(db)
            db.add(
                GroupMember(
                    group_id=group.id, user_id=user.id, org_id=org.id, tenant_id=org.tenant_id
                )
            )
            await _audit(join, audit_actions.GROUP_MEMBER_ADD, ("group", group.id), org.id)
            changed = True
    if changed:
        bump_version(org)
    if user.last_active_org_id is None:
        user.last_active_org_id = org.id
    await db.flush()
    return True


async def ensure_membership(
    join: Join, connection: SsoConnection, groups: tuple[str, ...], *, may_join: bool
) -> None:
    """Company membership (JIT when ``may_join``) and the mapped organization roles."""
    db, tenant, user = join.db, join.tenant, join.user
    member = await db.get(TenantMember, (tenant.id, user.id), populate_existing=True)
    if member is not None and member.status != "active":
        raise sso_error("sso_membership_blocked")
    sync = LazySync(tenant.id, (UserSubject(tenant.id, user.id),))  # capture on first change
    default: Organization | None = None
    if member is None:
        if not (connection.jit_enabled and may_join):
            raise sso_error("sso_not_invited")
        default = await _default_org(db, tenant.id)
        if default is None:
            raise sso_error("sso_no_organization")
        await sync.before_change(db)
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id))
        await db.flush()
        await _audit(join, audit_actions.TENANT_MEMBER_ADD, ("tenant_member", user.id), None)
    applied = [
        await _apply(join, target, sync)
        for target in mapping_targets(connection.group_role_mapping or {}, groups)
    ]
    if default is not None and not any(applied):
        await _apply(join, RoleTarget(default.id, connection.default_role), sync)
    await sync.queue(db)  # 7.2.1 "구성원 추가": JIT/SSO joins and mapped roles/groups
