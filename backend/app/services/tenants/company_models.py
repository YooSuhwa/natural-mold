"""Company models (``models.tenant_id``) and model designation (plan 7.2, 8.9, S07; E-07).

Platform models are registered by the platform operator; the company only narrows them
(company catalog policy, ``allow_system_models``, organization designation). Company models
are created, edited and deleted here and reach only the organizations designated for them.
A company keeps one default model (``uq_models_tenant_default`` in m81, kept here too); a
default credential must be this company's shared credential or, while platform keys are
allowed, a platform credential.
"""

from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.company_catalog import MODEL_ORGS_KEY, CompanyTier, company_tier, with_designation
from app.authz.error_codes import authorization_error
from app.exceptions import AppError
from app.models.model import Model
from app.models.organization import Tenant
from app.schemas.tenant_catalog import (
    TenantModelCreate,
    TenantModelList,
    TenantModelUpdate,
    TenantModelView,
)
from app.services import model_service
from app.services.system_credential_resolver import setting_credential
from app.services.tenants.catalog_common import (
    TenantCatalogMutation,
    commit_company_change,
    lock_for_write,
    record,
    require_company_orgs,
)
from app.tenancy.admin_context import TenantAdminContext


def model_view(model: Model, tier: CompanyTier) -> TenantModelView:
    source = "platform" if model.tenant_id is None else "company"
    designated = tier.model_orgs.get(model.id)
    return TenantModelView(
        id=model.id,
        source=source,
        provider=model.provider,
        model_name=model.model_name,
        display_name=model.display_name,
        base_url=model.base_url,
        is_default=model.is_default,
        is_visible=model.is_visible,
        default_credential_id=model.default_credential_id,
        company_enabled=tier.offers("model", model.id, source, available=model.is_visible),
        requires_company_key=source == "platform" and not tier.allow_system_models,
        allowed_org_ids=sorted(designated)
        if designated is not None
        else (None if source == "platform" else []),
    )


async def list_company_models(db: AsyncSession, context: TenantAdminContext) -> TenantModelList:
    """Visible platform models and every model of this company (no other company's)."""
    tier = company_tier(context.tenant)
    rows = await db.scalars(
        select(Model)
        .where(
            or_(
                Model.tenant_id == context.tenant.id,
                Model.tenant_id.is_(None) & Model.is_visible.is_(True),
            )
        )
        .order_by(Model.display_name, Model.id)
    )
    return TenantModelList(
        items=tuple(model_view(row, tier) for row in rows),
        allow_system_models=tier.allow_system_models,
    )


async def _company_model(db: AsyncSession, tenant: Tenant, model_id: UUID) -> Model:
    model = await db.scalar(
        select(Model).where(
            Model.id == model_id,
            or_(Model.tenant_id == tenant.id, Model.tenant_id.is_(None) & Model.is_visible),
        )
    )
    if model is None:
        raise authorization_error("not_found")
    return model


async def _check_fields(db: AsyncSession, tenant: Tenant, model: Model) -> None:
    if model.is_default and not model.is_visible:
        raise AppError(
            code="validation_error", message="cannot mark a hidden model as default", status=422
        )
    credential = model.default_credential_id
    if credential is not None and await setting_credential(db, credential, tenant=tenant) is None:
        raise authorization_error("not_found")
    if model.is_default:  # one default per company
        await db.execute(
            update(Model)
            .where(Model.tenant_id == tenant.id, Model.id != model.id)
            .values(is_default=False)
        )


async def create_company_model(
    mutation: TenantCatalogMutation, tenant_id: UUID, payload: TenantModelCreate
) -> TenantModelView:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    orgs = await require_company_orgs(mutation.db, tenant.id, payload.allowed_org_ids)
    fields = payload.model_dump(exclude={"allowed_org_ids"})
    model = Model(id=uuid4(), tenant_id=tenant.id, **fields)
    await _check_fields(mutation.db, tenant, model)
    mutation.db.add(model)
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, orgs)
    await _audit(mutation, tenant, model, "model_create", orgs)
    view = model_view(model, company_tier(tenant))
    await commit_company_change(mutation, tenant, "model", model.id)
    return view


async def update_company_model(
    mutation: TenantCatalogMutation, tenant_id: UUID, model_id: UUID, payload: TenantModelUpdate
) -> TenantModelView:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    model = await _company_model(mutation.db, tenant, model_id)
    platform = model.tenant_id is None
    fields = payload.model_fields_set - {"allowed_org_ids"}
    if platform and fields:  # a platform model is the operator's; the company only narrows
        raise authorization_error("permission_required")
    for name in sorted(fields):
        setattr(model, name, getattr(payload, name))
    if not platform:
        await _check_fields(mutation.db, tenant, model)
    orgs: list[UUID] | None = None
    if "allowed_org_ids" in payload.model_fields_set:
        requested = payload.allowed_org_ids
        if requested is not None or not platform:
            orgs = await require_company_orgs(mutation.db, tenant.id, requested or [])
        tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, orgs)
    await _audit(mutation, tenant, model, "model_update", orgs, sorted(fields))
    view = model_view(model, company_tier(tenant))
    await commit_company_change(mutation, tenant, "model", model.id)
    return view


async def delete_company_model(
    mutation: TenantCatalogMutation, tenant_id: UUID, model_id: UUID
) -> None:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    model = await _company_model(mutation.db, tenant, model_id)
    if model.tenant_id is None:
        raise authorization_error("permission_required")
    await model_service.ensure_model_unused(mutation.db, model.id)
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, None)
    await _audit(mutation, tenant, model, "model_delete", None)
    await mutation.db.delete(model)
    await commit_company_change(mutation, tenant, "model", model.id)


async def _audit(
    mutation: TenantCatalogMutation,
    tenant: Tenant,
    model: Model,
    change: str,
    orgs: list[UUID] | None,
    fields: list[str] | None = None,
) -> None:
    metadata: dict[str, object] = {
        "change": change,
        "source": "platform" if model.tenant_id is None else "company",
        "allowed_org_ids": None if orgs is None else [str(org) for org in orgs],
    }
    if fields is not None:
        metadata["changed_fields"] = fields
    target = ("model", model.id, model.display_name or model.model_name)
    await record(mutation, tenant, audit_actions.TENANT_CATALOG_UPDATE, target, metadata)
