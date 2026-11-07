"""The company memory policy screen (memory policy plan 15, Q9, 5.11, 5.6).

Company and personal tenants have the same keys; only the defaults differ
(``memory_policy.memory_policy_defaults``). A change needs ``can_manage_tenant`` (a
personal tenant's owner has it) and is audited by the keys it changed, never by values
that could carry memory text (there are none).
"""

from __future__ import annotations

from typing import Any, Final, cast

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import MEMORY_TENANT_POLICY_UPDATE
from app.authz.org_context import OrgContext
from app.exceptions import ValidationError
from app.models.organization import Tenant
from app.services import audit_service
from app.services.memory_policy import memory_policy_defaults, tenant_kind, tenant_memory_policy

KEYS: Final = (
    "default_mode",
    "allowed_modes",
    "consolidation_enabled",
    "channel_personal_memory",
    "contacts_allowed",
)


def values(tenant_settings: dict[str, Any] | None) -> dict[str, Any]:
    policy = tenant_memory_policy(tenant_settings)
    return {
        "default_mode": policy.default_mode,
        "allowed_modes": sorted(policy.allowed_modes),
        "consolidation_enabled": policy.consolidation_enabled,
        "channel_personal_memory": policy.channel_personal_memory,
        "contacts_allowed": policy.contacts_allowed,
    }


def defaults(tenant_settings: dict[str, Any] | None) -> dict[str, Any]:
    return memory_policy_defaults(tenant_kind(tenant_settings))


async def update(
    db: AsyncSession, tenant: Tenant, context: OrgContext, changes: dict[str, Any]
) -> dict[str, Any]:
    """Store ``changes`` (validated keys only) in ``tenants.settings["memory"]``."""
    current = values(tenant.settings)
    merged = {**current, **{key: changes[key] for key in KEYS if key in changes}}
    if merged["default_mode"] not in merged["allowed_modes"]:
        raise ValidationError(
            "MEMORY_POLICY_DEFAULT_NOT_ALLOWED", "기본 모드는 허용한 모드 중 하나여야 합니다"
        )
    changed = sorted(key for key in KEYS if merged[key] != current[key])
    if not changed:
        return current
    settings: dict[str, Any] = dict(tenant.settings or {})
    previous = settings.get("memory")
    stored: dict[str, Any] = dict(previous) if isinstance(previous, dict) else {}
    stored.update({key: merged[key] for key in changed})
    settings["memory"] = stored
    tenant.settings = cast("dict[str, JsonValue]", settings)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        owner_user_id=None,
        action=MEMORY_TENANT_POLICY_UPDATE,
        target_type="tenant",
        target_id=tenant.id,
        outcome="success",
        metadata={"changed": changed},
        tenant_id=tenant.id,
        org_id=context.org_id,
    )
    await db.commit()
    await db.refresh(tenant)
    return values(tenant.settings)


__all__ = ["KEYS", "defaults", "update", "values"]
