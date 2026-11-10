"""Company registration settings (tool-governance-plan 3.6): read and write.

Company members read; ``can_manage_tenant`` writes under the tenant ``updated_at`` lock
the other company policies share. Every change is audited with before/after values.
"""

from __future__ import annotations

from pydantic import JsonValue

from app.authz import registration
from app.authz.audit_actions import REGISTRATION_SETTINGS_UPDATED
from app.models.organization import Tenant
from app.schemas.guardrails import RegistrationRuleOut, RegistrationSettingsOut
from app.schemas.guardrails import RegistrationSettingsUpdate as Update
from app.services.guardrails_admin import CompanyMutation, _audit
from app.services.tenants.versioning import bump_timestamp, require_current_version
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)


def view(tenant: Tenant) -> RegistrationSettingsOut:
    rules = registration.rules_from_settings(tenant.settings)
    return RegistrationSettingsOut(
        kinds={
            kind: RegistrationRuleOut(
                who=rule.who,  # type: ignore[arg-type]
                review=rule.review,  # type: ignore[arg-type]
                member_sharing=rule.member_sharing,  # type: ignore[arg-type]
                reviewable=kind in registration.REVIEWED,
            )
            for kind, rule in rules.items()
        },
        saved=registration.SETTINGS_KEY in (tenant.settings or {}),
        updated_at=tenant.updated_at,
    )


async def update(
    mutation: CompanyMutation, context: TenantAdminContext, payload: Update
) -> RegistrationSettingsOut:
    db = mutation.db
    fresh = await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, permission="can_manage_tenant")
    )
    tenant = fresh.tenant
    require_current_version(tenant.updated_at, mutation.expected_version)
    current = registration.rules_from_settings(tenant.settings)
    before = registration.rules_to_settings(current)
    rules = dict(current)
    for kind, rule in payload.kinds.items():
        review = rule.review if kind in registration.REVIEWED else "off"
        rules[kind] = registration.Rule(rule.who, review, rule.member_sharing)
    after = registration.rules_to_settings(rules)
    saved: dict[str, JsonValue] = {kind: dict(rule) for kind, rule in after.items()}
    tenant.settings = {**(tenant.settings or {}), registration.SETTINGS_KEY: saved}
    tenant.updated_at = bump_timestamp(tenant.updated_at)
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        REGISTRATION_SETTINGS_UPDATED,
        ("tenant", tenant.id, tenant.name),
        before,
        after,
    )
    result = view(tenant)
    await db.commit()
    return result
