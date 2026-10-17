"""Who may read and set each usage limit (plan 8.7 "정하는 사람", 9.7 ``/api/usage-limits``; H-06).

=========  =====================================  ===========================================
scope      read                                   write
=========  =====================================  ===========================================
tenant     ``can_view_tenant_usage``              ``can_manage_tenant``, installed
                                                  (``single_tenant``) only — in SaaS the
                                                  operator sets it (``/api/admin/tenants``)
org        ``can_view_usage`` of that org         company admin (``can_manage_tenant``)
user       the user themself, or                  ``can_manage_members`` of the org (org
           ``can_view_usage`` of the org          admins, company admins)
agent      ``can_view_usage`` of the agent's org  ``can_manage_policy`` of the agent's org
=========  =====================================  ===========================================

A user limit lives in one organization (the caller's current one unless ``org_id`` is
given). A user reading their own limit without ``can_view_usage`` gets no higher amounts
(``oversight=False``: no ``parent_limit``). Missing or foreign targets are 404; visible
targets without the permission 403 ``permission_required``; hidden agents
(``runtime_profile`` other than standard) are 404.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.org_control import OrgControlActor, OrgControlTarget, load_org_control
from app.authz.org_oversight import load_org_oversight
from app.config import settings
from app.database import SessionTenant, bind_tenant
from app.exceptions import AppError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.organization import Organization, OrganizationMember
from app.schemas.usage_limits import LimitScope
from app.tenancy.context import resolve_tenant_context
from app.tenancy.permissions import require_tenant_permission, tenant_permissions


@dataclass(frozen=True, slots=True)
class LimitTarget:
    scope: LimitScope
    scope_id: UUID
    tenant_id: UUID
    org_id: UUID | None = None
    oversight: bool = True


async def _tenant(db: AsyncSession, actor: OrgControlActor, tenant_id: UUID, write: bool) -> None:
    company = await resolve_tenant_context(db, actor, tenant_id)
    if not write:
        require_tenant_permission(company, "can_view_tenant_usage")
        return
    if settings.deployment_mode != "single_tenant" or "can_manage_tenant" not in (
        tenant_permissions(company)
    ):
        raise authorization_error("permission_required")


async def _org_for_write(db: AsyncSession, actor: OrgControlActor, org_id: UUID) -> Organization:
    organization = await db.get(Organization, org_id)
    if organization is None:
        raise authorization_error("not_found")
    company = await resolve_tenant_context(db, actor, organization.tenant_id)
    if "can_manage_tenant" not in tenant_permissions(company):
        raise authorization_error("permission_required")
    return organization


async def _require_member(db: AsyncSession, org_id: UUID, user_id: UUID) -> None:
    member = await db.scalar(
        select(OrganizationMember.user_id).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user_id,
            OrganizationMember.status != "removed",
        )
    )
    if member is None:
        raise authorization_error("not_found")


async def _user(
    db: AsyncSession, actor: OrgControlActor, user_id: UUID, org_id: UUID, write: bool
) -> tuple[UUID, bool]:
    oversight = True
    if write:
        control = await load_org_control(
            db, actor, OrgControlTarget(org_id, "can_manage_members", lock_organization=False)
        )
        tenant_id = control.organization.tenant_id
    elif user_id == actor.id:
        await _require_member(db, org_id, user_id)
        organization = await db.get(Organization, org_id)
        assert organization is not None  # noqa: S101 - membership implies the row
        tenant_id = organization.tenant_id
        try:
            await load_org_oversight(db, actor, org_id, "can_view_usage")
        except AppError:
            oversight = False
    else:
        tenant_id = (await load_org_oversight(db, actor, org_id, "can_view_usage")).tenant_id
    await _require_member(db, org_id, user_id)
    return tenant_id, oversight


async def _agent(
    db: AsyncSession, actor: OrgControlActor, agent_id: UUID, write: bool
) -> tuple[UUID, UUID]:
    row = (
        await db.execute(
            select(Agent.org_id, Agent.tenant_id).where(
                Agent.id == agent_id, Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD
            )
        )
    ).one_or_none()
    if row is None or row.org_id is None or row.tenant_id is None:
        raise authorization_error("not_found")
    if write:
        await load_org_control(
            db, actor, OrgControlTarget(row.org_id, "can_manage_policy", lock_organization=False)
        )
    else:
        await load_org_oversight(db, actor, row.org_id, "can_view_usage")
    return row.tenant_id, row.org_id


async def resolve_limit_target(
    db: AsyncSession,
    actor: OrgControlActor,
    scope: LimitScope,
    scope_id: UUID,
    *,
    current: OrgContext,
    org_id: UUID | None = None,
    write: bool = False,
) -> LimitTarget:
    """Check the caller's right on one limit and bind the session to its company."""
    if scope == "tenant":
        await _tenant(db, actor, scope_id, write)
        target = LimitTarget(scope, scope_id, scope_id)
    elif scope == "org":
        if write:
            tenant_id = (await _org_for_write(db, actor, scope_id)).tenant_id
        else:
            tenant_id = (await load_org_oversight(db, actor, scope_id, "can_view_usage")).tenant_id
        target = LimitTarget(scope, scope_id, tenant_id, scope_id)
    elif scope == "user":
        org = org_id or current.org_id
        tenant_id, oversight = await _user(db, actor, scope_id, org, write)
        target = LimitTarget(scope, scope_id, tenant_id, org, oversight)
    else:
        tenant_id, agent_org = await _agent(db, actor, scope_id, write)
        target = LimitTarget(scope, scope_id, tenant_id, agent_org)
    await bind_tenant(db, SessionTenant(tenant_id=target.tenant_id, user_id=actor.id))
    return target


__all__ = ["LimitTarget", "resolve_limit_target"]
