"""``GET|PUT /api/tenants/{tenant_id}/policies`` (plan 9.7, S05).

Only the company publication policy is stored here so far (E-09); reading needs company
membership, writing ``can_manage_tenant`` with the tenant ``updated_at`` as ``If-Match``.
"""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.dependencies import CurrentUser
from app.marketplace.tenant_publication_policy import publish_policy, with_publish_policy
from app.models.organization import Tenant
from app.schemas.tenant_publication import TenantPolicies, TenantPoliciesUpdate
from app.services import audit_service
from app.services.tenants.versioning import bump_timestamp, require_current_version, utc_timestamp
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)


@dataclass(frozen=True, slots=True)
class TenantPolicyMutation:
    db: AsyncSession
    request: Request
    expected_version: str | None
    actor: CurrentUser


def tenant_policies(tenant: Tenant) -> TenantPolicies:
    return TenantPolicies(
        marketplace_tenant_publish=publish_policy(tenant),
        updated_at=utc_timestamp(tenant.updated_at),
    )


async def update_tenant_policies(
    mutation: TenantPolicyMutation, context: TenantAdminContext, payload: TenantPoliciesUpdate
) -> TenantPolicies:
    db = mutation.db
    fresh = await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, permission="can_manage_tenant")
    )
    tenant = fresh.tenant
    require_current_version(tenant.updated_at, mutation.expected_version)
    before = publish_policy(tenant)
    if payload.marketplace_tenant_publish == before:
        return tenant_policies(tenant)
    try:
        with_publish_policy(tenant, payload.marketplace_tenant_publish)
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=fresh.actor.id,
            actor_email_snapshot=mutation.actor.email,
            owner_user_id=fresh.actor.id,
            action=audit_actions.POLICY_MARKETPLACE_PUBLISH_UPDATE,
            target_type="tenant",
            target_id=tenant.id,
            target_name_snapshot=tenant.name,
            outcome="success",
            request=mutation.request,
            metadata={
                "tenant_id": str(tenant.id),
                "before": before,
                "after": payload.marketplace_tenant_publish,
            },
        )
        result = tenant_policies(tenant)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return result
