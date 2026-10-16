"""Company service LLM ``GET|PUT /api/tenants/{id}/llm-settings`` (8.9, S07; E-07).

Each role either inherits the platform default (only while the company may use platform
keys, ``allow_system_models``) or is overridden by a company row
(``system_llm_settings.tenant_id``) naming this company's shared credential or, while
allowed, a platform credential. ``resolve_system_model(..., tenant_id=)`` implements the
same order, but today's runtime callers (builder, assistant, image, follow-up, skill
builder) still pass no ``tenant_id`` and read the platform default; they are wired to the
company in the F-phase.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.company_catalog import allows_system_models
from app.authz.error_codes import authorization_error
from app.exceptions import AppError
from app.models.organization import Tenant
from app.models.system_llm_setting import SYSTEM_LLM_ROLES, SystemLlmSetting
from app.schemas.tenant_catalog import (
    TenantLlmSettingsView,
    TenantLlmSettingUpdate,
    TenantLlmSettingView,
)
from app.services.system_credential_resolver import get_setting, setting_credential
from app.services.tenants.catalog_common import TenantCatalogMutation, lock_for_write, record
from app.tenancy.admin_context import TenantAdminContext

# Mirrors the platform screen (routers/system_llm_settings.py, ADR-019).
LLM_DEFINITION_KEYS = frozenset({"openai", "anthropic", "openrouter", "openai_compatible"})


async def _role_view(db: AsyncSession, tenant: Tenant, role: str) -> TenantLlmSettingView:
    company = await get_setting(db, role, tenant_id=tenant.id)
    inherits = company is None
    effective = company
    if inherits and allows_system_models(tenant):
        effective = await get_setting(db, role)
    credential = None
    if effective is not None and effective.credential_id is not None:
        owner = None if inherits else tenant
        credential = await setting_credential(db, effective.credential_id, tenant=owner)
    model_name = effective.model_name if effective is not None else None
    return TenantLlmSettingView(
        role=role,
        inherits=inherits,
        credential_id=credential.id if credential is not None else None,
        credential_name=credential.name if credential is not None else None,
        provider=credential.definition_key if credential is not None else None,
        model_name=model_name,
        configured=credential is not None and bool(model_name),
    )


async def _view(db: AsyncSession, tenant: Tenant) -> TenantLlmSettingsView:
    return TenantLlmSettingsView(
        items=tuple([await _role_view(db, tenant, role) for role in SYSTEM_LLM_ROLES]),
        allow_system_models=allows_system_models(tenant),
    )


async def read_llm_settings(db: AsyncSession, context: TenantAdminContext) -> TenantLlmSettingsView:
    return await _view(db, context.tenant)


async def update_llm_setting(
    mutation: TenantCatalogMutation, tenant_id: UUID, payload: TenantLlmSettingUpdate
) -> TenantLlmSettingsView:
    db = mutation.db
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    if (payload.credential_id is None) != (payload.model_name is None):
        raise AppError(
            code="validation_error",
            message="credential_id and model_name are set together (or both null to inherit)",
            status=422,
        )
    row = await get_setting(db, payload.role, tenant_id=tenant.id)
    provider = None
    if payload.credential_id is None:
        if row is not None:
            await db.delete(row)
    else:
        credential = await setting_credential(db, payload.credential_id, tenant=tenant)
        if credential is None:
            raise authorization_error("not_found")
        if credential.definition_key not in LLM_DEFINITION_KEYS:
            raise AppError(
                code="validation_error", message="credential is not an LLM provider", status=422
            )
        provider = credential.definition_key
        if row is None:
            row = SystemLlmSetting(role=payload.role, tenant_id=tenant.id)
            db.add(row)
        row.credential_id, row.model_name = credential.id, payload.model_name
    await record(
        mutation,
        tenant,
        audit_actions.TENANT_LLM_SETTINGS_UPDATE,
        ("tenant", tenant.id, tenant.name),
        {
            "role": payload.role,
            "inherits": payload.credential_id is None,
            "provider": provider,
            "model_name": payload.model_name,
        },
    )
    await db.flush()
    view = await _view(db, tenant)
    await db.commit()
    return view
