"""``GET|PUT /api/tenants/{tenant_id}/policies`` (plan 9.7, S05).

The company publication policy (E-09) and the company audit retention (E-08, 14.1);
reading needs company membership, writing ``can_manage_tenant`` with the tenant
``updated_at`` as ``If-Match``. Shortening the audit retention without ``confirm=true``
saves nothing and returns the number of audit rows the next sweep would delete.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.dependencies import CurrentUser
from app.marketplace.tenant_publication_policy import publish_policy, with_publish_policy
from app.models.organization import Tenant
from app.schemas.retention import AUDIT_SETTING, RetentionPreview, audit_retention_days
from app.schemas.tenant_publication import TenantPolicies, TenantPoliciesUpdate
from app.services import audit_service
from app.services.retention.targets import count, expired_audit
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


def tenant_policies(tenant: Tenant, preview: RetentionPreview | None = None) -> TenantPolicies:
    return TenantPolicies(
        marketplace_tenant_publish=publish_policy(tenant),
        audit_retention_days=audit_retention_days(tenant.settings),
        updated_at=utc_timestamp(tenant.updated_at),
        preview=preview,
    )


async def _audit(
    mutation: TenantPolicyMutation, tenant: Tenant, action: str, change: tuple[object, object]
) -> None:
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=mutation.actor.id,
        actor_email_snapshot=mutation.actor.email,
        owner_user_id=mutation.actor.id,
        action=action,
        target_type="tenant",
        target_id=tenant.id,
        target_name_snapshot=tenant.name,
        outcome="success",
        request=mutation.request,
        tenant_id=tenant.id,
        metadata={"tenant_id": str(tenant.id), "before": change[0], "after": change[1]},
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
    publish = (publish_policy(tenant), payload.marketplace_tenant_publish)
    days_before = audit_retention_days(tenant.settings)
    days = (days_before, payload.audit_retention_days or days_before)
    if publish[0] == publish[1] and days[0] == days[1]:
        return tenant_policies(tenant)
    if days[1] < days[0] and not payload.confirm:
        expired = await count(db, expired_audit(tenant.id, days[1], datetime.now(UTC)))
        result = tenant_policies(tenant, RetentionPreview(audit_events=expired))
        await db.rollback()
        return result
    try:
        if publish[0] != publish[1]:
            with_publish_policy(tenant, publish[1])
            await _audit(mutation, tenant, audit_actions.POLICY_MARKETPLACE_PUBLISH_UPDATE, publish)
        if days[0] != days[1]:
            tenant.settings = {**(tenant.settings or {}), AUDIT_SETTING: days[1]}
            await _audit(mutation, tenant, audit_actions.POLICY_RETENTION_UPDATE, days)
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        result = tenant_policies(tenant)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return result
