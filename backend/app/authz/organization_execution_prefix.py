"""Canonical execution prefix: Tenant -> Organization -> owner memberships -> User.

Locks each row (shared, claimed, cursor-ordered) and freshly requires an
active Tenant, both active owner memberships and an active user. The
Organization row is returned for the caller's status decision.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionLockCursor, LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_locks import lock_row
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User


def forbidden() -> AppError:
    return AppError(code="forbidden", message="권한이 없습니다", status=403)


def unavailable() -> AppError:
    return AppError(
        code="organization_unavailable", message="사용할 수 없는 조직입니다", status=403
    )


ORG_WRITE_INTENT = "moldy_org_write_intent"


def mark_org_writer(db: AsyncSession) -> None:
    """Writer mode for this session (F-02): the prefix takes the Organization row
    ``FOR UPDATE`` instead of ``FOR SHARE``. A link/grant write later locks or bumps that
    row (delegation lock, revocation epoch); taking it exclusively up front avoids a
    share→exclusive upgrade, which deadlocks two concurrent saves of one organization.
    Readers never set it."""
    db.sync_session.info[ORG_WRITE_INTENT] = True


async def lock_execution_prefix(
    db: AsyncSession, cursor: ExecutionLockCursor, context: OrgContext
) -> Organization:
    tenant = await lock_row(
        db,
        cursor,
        LockClass.TENANT,
        context.tenant_id,
        select(Tenant).where(Tenant.id == context.tenant_id).with_for_update(read=True),
    )
    if tenant is None:
        raise forbidden()
    if tenant.status == "suspended":
        raise AppError(code="tenant_suspended", message="회사가 정지되었습니다", status=403)
    if tenant.status != "active":
        raise unavailable()
    organization = await lock_row(
        db,
        cursor,
        LockClass.ORGANIZATION,
        context.org_id,
        select(Organization)
        .where(Organization.id == context.org_id, Organization.tenant_id == tenant.id)
        .with_for_update(read=not db.sync_session.info.get(ORG_WRITE_INTENT, False)),
    )
    if organization is None:
        raise forbidden()
    tenant_member = await lock_row(
        db,
        cursor,
        LockClass.TENANT_MEMBER,
        context.user_id,
        select(TenantMember)
        .where(TenantMember.tenant_id == tenant.id, TenantMember.user_id == context.user_id)
        .with_for_update(read=True),
    )
    org_member = await lock_row(
        db,
        cursor,
        LockClass.ORGANIZATION_MEMBER,
        context.user_id,
        select(OrganizationMember)
        .where(
            OrganizationMember.org_id == organization.id,
            OrganizationMember.user_id == context.user_id,
        )
        .with_for_update(read=True),
    )
    if (
        tenant_member is None
        or tenant_member.status != "active"
        or org_member is None
        or org_member.status != "active"
    ):
        raise forbidden()
    user = await lock_row(
        db,
        cursor,
        LockClass.USER,
        context.user_id,
        select(User).where(User.id == context.user_id).with_for_update(read=True, key_share=True),
    )
    if user is None or not user.is_active:
        raise AppError(code="not_found", message="리소스를 찾을 수 없습니다", status=404)
    return organization
