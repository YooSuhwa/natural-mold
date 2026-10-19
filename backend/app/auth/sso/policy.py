"""``tenants.settings.sso`` (plan 10.10 "SSO 필수"): mode and break-glass user ids.

The login/refresh gates that enforce it live in ``app.auth.sso.session_gate``.
"""

from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from pydantic import JsonValue

from app.models.organization import Tenant

SSO_SETTING: Final = "sso"
SsoMode = Literal["off", "optional", "required"]
MAX_BREAK_GLASS: Final = 2


@dataclass(frozen=True, slots=True)
class SsoPolicy:
    mode: SsoMode = "off"
    break_glass_user_ids: tuple[UUID, ...] = ()


def sso_policy(tenant: Tenant) -> SsoPolicy:
    raw = (tenant.settings or {}).get(SSO_SETTING)
    if not isinstance(raw, dict):
        return SsoPolicy()
    mode = raw.get("mode")
    ids: list[UUID] = []
    values = raw.get("break_glass_user_ids")
    for value in values if isinstance(values, list) else []:
        try:
            ids.append(UUID(str(value)))
        except ValueError:
            continue
    return SsoPolicy(
        mode=mode if mode in ("off", "optional", "required") else "off",
        break_glass_user_ids=tuple(ids),
    )


def store_sso_policy(tenant: Tenant, policy: SsoPolicy) -> None:
    value: dict[str, JsonValue] = {
        "mode": policy.mode,
        "break_glass_user_ids": [str(user_id) for user_id in policy.break_glass_user_ids],
    }
    tenant.settings = {**(tenant.settings or {}), SSO_SETTING: value}
