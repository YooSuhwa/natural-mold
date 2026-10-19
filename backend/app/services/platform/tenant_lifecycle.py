"""SaaS operator company suspension and resume (parent Task 13).

Only ``tenants.status``/``updated_at`` change, together with one audit event in
the same transaction. Memberships, role grants, refresh tokens, organizations,
triggers, deployments and accepted running work are never touched: suspension
is enforced by the fresh request-time checks (``ensure_tenant_active``,
``resolve_org_context`` and the Task-10 execution prefix), which deny every new
company request with ``403 tenant_suspended``. Resume restores ``active`` only,
so archived organizations and paused entry points stay exactly as they were.

Queued and accepted work is deliberately not terminalized (lead decision, B08
rule): suspension is reversible, so pending inputs stay pending, running runs
keep running until their next prepared dispatch permit is refused with
``tenant_suspended``, and nothing is claimed while suspended; after resume the
same inputs are claimable again. Only archive/member-exit terminalize work.

Agent API keys are the one entry that does not surface ``tenant_suspended``:
``app.authz.api_key_actor`` (B05) keeps its opaque key contract and answers
``401 AGENT_API_KEY_INVALID`` for any revoked owner authority, suspension
included, so a key never discloses its company's state.

Order: Tenant row lock -> current ``If-Match`` -> repeated target state is an
idempotent no-op (same version, no audit) -> allowed source state -> write.
"""

from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.exceptions import AppError
from app.models.organization import Tenant
from app.schemas.admin_tenant import AdminTenantDetail
from app.services import audit_service
from app.services.platform.tenant_directory import PlatformTenantMutation, get_platform_tenant
from app.services.tenants.versioning import bump_timestamp, require_current_version

TenantLifecycleAction = Literal["suspend", "resume"]


@dataclass(frozen=True, slots=True)
class _Transition:
    source: str
    target: str
    audit_action: str


_TRANSITIONS: Final[dict[TenantLifecycleAction, _Transition]] = {
    "suspend": _Transition("active", "suspended", audit_actions.TENANT_SUSPEND),
    "resume": _Transition("suspended", "active", audit_actions.TENANT_RESUME),
}


async def change_platform_tenant_status(
    mutation: PlatformTenantMutation,
    action: TenantLifecycleAction,
    reason: str | None = None,
) -> AdminTenantDetail:
    """Apply one suspend/resume transition and its audit atomically."""
    transition = _TRANSITIONS[action]
    db = mutation.db
    try:
        with db.no_autoflush:
            tenant = await db.scalar(
                select(Tenant)
                .where(Tenant.id == mutation.tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if tenant is None:
            raise authorization_error("not_found")
        require_current_version(tenant.updated_at, mutation.expected_version)
        if tenant.status == transition.target:
            await db.rollback()
            return await get_platform_tenant(db, mutation.tenant_id)
        if tenant.status != transition.source:
            raise authorization_error("tenant_status_conflict")
        tenant.status = transition.target
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=mutation.actor.id,
            actor_email_snapshot=mutation.actor.email,
            owner_user_id=mutation.actor.id,
            action=transition.audit_action,
            target_type="tenant",
            target_id=tenant.id,
            target_name_snapshot=tenant.name,
            outcome="success",
            reason_message=reason,
            request=mutation.request,
            metadata={
                "tenant_id": str(tenant.id),
                "previous_status": transition.source,
                "status": transition.target,
            },
        )
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
    return await get_platform_tenant(db, mutation.tenant_id)
