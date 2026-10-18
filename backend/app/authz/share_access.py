"""``GET /api/resources/{type}/{id}/access``: who can reach a resource and why (9.7, S16).

Rows: the owner (``source=owner``), every active share grant (``direct`` for a user,
``group`` for a group with its member count, ``organization`` for the whole
organization) and the governors — organization admins and company admins — who may
govern (transfer, revoke) without content access (``source=governance``, D8). Only grant
rows are revocable; ownership moves by transfer. For an agent, ``delegations`` lists the
account resources it lends (D17), so the dialog can show them before sharing (E-06).
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.delegation_accounts import delegated_accounts
from app.authz.org_administrators import active_administrators
from app.authz.org_context import OrgContext
from app.authz.principal_names import user_display_names
from app.authz.service import AuthzService
from app.authz.share_targets import ShareType, authorize_target, my_share_permissions
from app.authz.sharing import share_grants
from app.models.organization import Organization, TenantMember, TenantRoleGrant
from app.models.organization_group import Group, GroupMember
from app.models.user import User
from app.schemas.grant import AccessEntry, AccessList, PrincipalView

_SOURCE = {"user": "direct", "group": "group", "organization": "organization"}


async def _company_admins(db: AsyncSession, tenant_id: UUID) -> set[UUID]:
    found = await db.scalars(
        select(TenantRoleGrant.user_id)
        .join(
            TenantMember,
            (TenantMember.tenant_id == TenantRoleGrant.tenant_id)
            & (TenantMember.user_id == TenantRoleGrant.user_id),
        )
        .join(User, User.id == TenantRoleGrant.user_id)
        .where(
            TenantRoleGrant.tenant_id == tenant_id,
            TenantRoleGrant.role == "admin",
            TenantMember.status == "active",
            User.is_active.is_(True),
        )
    )
    return set(found)


async def _group_names(db: AsyncSession, ids: set[UUID]) -> dict:
    if not ids:
        return {}
    rows = await db.execute(select(Group.id, Group.name).where(Group.id.in_(ids)))
    return dict(rows.tuples().all())


async def _group_sizes(db: AsyncSession, ids: set[UUID]) -> dict[UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(GroupMember.group_id, func.count())
        .where(GroupMember.group_id.in_(ids))
        .group_by(GroupMember.group_id)
    )
    return dict(rows.tuples().all())


async def access_list(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, kind: ShareType, resource_id: UUID
) -> AccessList:
    row = await authorize_target(db, authz, ctx, kind, resource_id, "can_revoke")
    owner = row.user_id
    if owner is None:  # unreachable: load_target refuses owner-less rows
        raise denials.not_found()
    grants = await share_grants(db, ctx, kind, row.id)
    organization = await db.get(Organization, ctx.org_id)
    governors = await _company_admins(db, ctx.tenant_id)
    if organization is not None:
        governors |= await active_administrators(db, organization)
    governors.discard(owner)
    users = {g.principal_id for g in grants if g.principal_type == "user"} | governors | {owner}
    groups = {g.principal_id for g in grants if g.principal_type == "group"}
    # The profile name, not the registration name (a renamed owner showed "E2E User").
    user_names = await user_display_names(db, users)
    group_names, sizes = await _group_names(db, groups), await _group_sizes(db, groups)
    org_name = organization.name if organization is not None else None

    def principal(kind_: str, pid: UUID) -> PrincipalView:
        names = {"user": user_names, "group": group_names}.get(kind_)
        name = org_name if names is None else names.get(pid)
        size = sizes.get(pid, 0) if kind_ == "group" else None
        return PrincipalView.model_validate(
            {"type": kind_, "id": pid, "display_name": name, "member_count": size}
        )

    items = [
        AccessEntry(
            grant_id=None,
            principal=principal("user", owner),
            level="owner",
            source="owner",
            revocable=False,
        )
    ]
    items += [
        AccessEntry.model_validate(
            {
                "grant_id": g.id,
                "principal": principal(g.principal_type, g.principal_id),
                "level": g.relation,
                "source": _SOURCE[g.principal_type],
                "revocable": True,
                "expires_at": g.expires_at,
            }
        )
        for g in grants
    ]
    items += [
        AccessEntry(
            grant_id=None,
            principal=principal("user", uid),
            level="governor",
            source="governance",
            revocable=False,
        )
        for uid in sorted(governors, key=str)
    ]
    permissions = await my_share_permissions(authz, ctx, kind, row)
    # D17 list for whoever can share (E-06); governors/managers without can_share see none.
    sharer = kind.name == "agent" and "can_share" in permissions
    delegations = await delegated_accounts(db, row.id) if sharer else []
    return AccessList.model_validate(
        {"items": items, "my_permissions": permissions, "delegations": delegations}
    )
