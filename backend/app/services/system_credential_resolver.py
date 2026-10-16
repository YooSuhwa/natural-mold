"""Single source of truth for "operator-managed API key" lookups.

Used by every system-billed flow:
- Fix Agent (``app.agent_runtime.assistant.assistant_agent``)
- Image generation (``app.services.image_service``,
  ``app.agent_runtime.builder_v3.image_gen``)
- Future builder / bootstrap flows

Tiered policy (mirror of the user-side
:func:`app.services.credential_resolver.resolve_credential_for_model`):

  1. ENV (``PROVIDER_API_KEY_MAP``) — bootstrap convenience so a clean
     install with only ``.env`` keeps working.
  2. ``Credential`` row with ``is_system=True`` matching the provider —
     operator manages keys via ``/settings/system-credentials``.
  3. ``None`` — caller surfaces the resulting LLM error.

User credentials are intentionally NOT consulted. System functions bill
the operator, not whichever user happens to be logged in.

Per-company service LLM (plan 8.9 시스템 LLM 설정, 10.x; E-07): with ``tenant_id`` the
company override row wins; a company without one inherits the platform default only
while its ``allow_system_models`` limit permits platform keys. A company row may name a
company shared credential of the same company, or a platform credential when allowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_factory import PROVIDER_API_KEY_MAP
from app.authz.company_catalog import allows_system_models
from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.organization import Tenant
from app.models.system_llm_setting import SystemLlmSetting

logger = logging.getLogger(__name__)


class SystemModelNotConfiguredError(Exception):
    """Raised when a system LLM role has no credential/model selected.

    Per ADR-019 §결정2 there is no silent ``.env`` fallback — a missing
    operator selection surfaces explicitly so configuration gaps never hide.
    """

    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(
            f"System LLM role '{role}' is not configured. "
            "An operator must complete the System LLM settings."
        )


@dataclass(frozen=True)
class ResolvedSystemModel:
    """Everything needed to build a chat/image model for a system role."""

    provider: str  # credential.definition_key (anthropic|openai|openrouter|openai_compatible)
    model_name: str
    api_key: str | None
    base_url: str | None


async def resolve_system_api_key(db: AsyncSession, provider: str) -> str | None:
    """ENV → ``is_system=True`` Credential lookup → ``None``."""

    env_key = PROVIDER_API_KEY_MAP.get(provider)
    if env_key:
        return env_key

    cred = await credential_service.find_system_by_definition(db, provider)
    if cred is None:
        return None
    try:
        payload = await credential_service.decrypt_with_external(cred)
    except Exception:  # noqa: BLE001
        logger.exception("System credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


async def get_setting(
    db: AsyncSession, role: str, *, tenant_id: UUID | None = None
) -> SystemLlmSetting | None:
    """The platform row for ``role`` (or ``tenant_id``'s override row), or ``None``."""
    scope = (
        SystemLlmSetting.tenant_id.is_(None)
        if tenant_id is None
        else SystemLlmSetting.tenant_id == tenant_id
    )
    result = await db.execute(select(SystemLlmSetting).where(SystemLlmSetting.role == role, scope))
    return result.scalar_one_or_none()


async def setting_credential(
    db: AsyncSession, credential_id: UUID, *, tenant: Tenant | None
) -> Credential | None:
    """A platform credential, or (with ``tenant``) one of its shared credentials; a platform
    credential for a company only while the company may use platform keys."""
    if tenant is not None:
        company = await db.scalar(
            select(Credential).where(
                Credential.id == credential_id,
                Credential.scope == "tenant",
                Credential.tenant_id == tenant.id,
                Credential.org_id.is_(None),
            )
        )
        if company is not None or not allows_system_models(tenant):
            return company
    return await credential_service.get_system(db, credential_id)


async def resolve_system_model(
    db: AsyncSession, role: str, *, tenant_id: UUID | None = None
) -> ResolvedSystemModel:
    """Resolve the operator-selected model for a system ``role``.

    ADR-019 §결정3. Reads the role's ``system_llm_settings`` row, loads the
    referenced ``is_system`` credential, and decrypts it to extract api_key /
    base_url. ``provider`` is the credential's ``definition_key`` (single
    source of truth). Raises :class:`SystemModelNotConfiguredError` if the
    role has no credential or model selected, or the credential is missing.
    """

    tenant = None if tenant_id is None else await db.get(Tenant, tenant_id)
    setting = None
    if tenant is not None:
        setting = await get_setting(db, role, tenant_id=tenant.id)
        configured = setting is not None and setting.credential_id and setting.model_name
        if not configured:
            if not allows_system_models(tenant):
                raise SystemModelNotConfiguredError(role)
            setting, tenant = None, None  # inherit the platform default
    if setting is None:
        setting = await get_setting(db, role)
    if setting is None or setting.credential_id is None or not setting.model_name:
        raise SystemModelNotConfiguredError(role)

    cred = await setting_credential(db, setting.credential_id, tenant=tenant)
    if cred is None:
        # Credential deleted between selection and use (SET NULL not yet
        # applied, or race). Treat as unconfigured.
        raise SystemModelNotConfiguredError(role)

    payload = await credential_service.decrypt_with_external(cred)
    api_key = payload.get("api_key") or payload.get("token")
    base_url = payload.get("base_url")
    return ResolvedSystemModel(
        provider=cred.definition_key,
        model_name=setting.model_name,
        api_key=str(api_key) if api_key else None,
        base_url=str(base_url) if base_url else None,
    )


__all__ = [
    "ResolvedSystemModel",
    "SystemModelNotConfiguredError",
    "get_setting",
    "resolve_system_api_key",
    "resolve_system_model",
]
