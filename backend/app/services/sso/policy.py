"""``GET|PUT /api/tenants/{tenant_id}/sso-policy`` (plan 10.10, S30 "SSO 필수").

``required`` needs an active connection, a verified domain and 1–2 break-glass users
who are active company administrators. Switching to ``required`` revokes the members'
password-origin refresh tokens (break-glass users excepted); access tokens already
issued run out on their own short TTL. ``password_members`` is the S30 notice count
("켜면 비밀번호로 로그인하던 구성원 N명이 ...").
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.errors import sso_error
from app.auth.sso.policy import SsoPolicy, sso_policy, store_sso_policy
from app.authz import audit_actions
from app.authz.sso_members import (
    active_admin_ids,
    password_member_count,
    revoke_password_sessions,
)
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection, TenantDomain
from app.schemas.sso import SsoPolicyRead, SsoPolicyUpdate
from app.services.sso.common import SsoMutation, audit, locked_admin
from app.services.tenants.versioning import bump_timestamp
from app.tenancy.admin_context import TenantAdminContext


async def policy_view(db: AsyncSession, tenant: Tenant) -> SsoPolicyRead:
    policy = sso_policy(tenant)
    return SsoPolicyRead(
        mode=policy.mode,
        break_glass_user_ids=list(policy.break_glass_user_ids),
        password_members=await password_member_count(db, tenant.id, policy.break_glass_user_ids),
    )


async def _exists(db: AsyncSession, query: Select[tuple[UUID]]) -> bool:
    return (await db.scalar(query.limit(1))) is not None


async def _admins(db: AsyncSession, tenant_id: UUID, user_ids: list[UUID]) -> bool:
    if len(set(user_ids)) != len(user_ids):
        return False
    return await active_admin_ids(db, tenant_id, user_ids) == set(user_ids)


async def update_policy(
    mutation: SsoMutation, context: TenantAdminContext, payload: SsoPolicyUpdate
) -> SsoPolicyRead:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    before = sso_policy(tenant)
    ids = list(payload.break_glass_user_ids)
    if ids and not await _admins(db, tenant.id, ids):
        raise sso_error("sso_policy_incomplete")
    if payload.mode == "required":
        active = select(SsoConnection.id).where(
            SsoConnection.tenant_id == tenant.id, SsoConnection.status == "active"
        )
        verified = select(TenantDomain.id).where(
            TenantDomain.tenant_id == tenant.id, TenantDomain.status == "verified"
        )
        if not ids or not await _exists(db, active) or not await _exists(db, verified):
            raise sso_error("sso_policy_incomplete")
    after = SsoPolicy(mode=payload.mode, break_glass_user_ids=tuple(ids))
    if after != before:
        if after.mode == "required" and before.mode != "required":
            await revoke_password_sessions(db, tenant.id, after.break_glass_user_ids)
        store_sso_policy(tenant, after)
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        await audit(
            mutation,
            tenant,
            audit_actions.TENANT_SSO_POLICY_UPDATE,
            ("tenant", tenant.id, tenant.name),
            {
                "before": before.mode,
                "after": after.mode,
                "break_glass_user_ids": [str(user_id) for user_id in ids],
            },
        )
        await db.flush()
    result = await policy_view(db, tenant)
    await db.commit()
    return result
