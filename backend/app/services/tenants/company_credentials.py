"""Company shared credentials (``credentials.scope = 'tenant'``; plan 6.2, 7.2, S07; E-07).

A company credential has no owner and no organization (``is_system`` with the company's
``tenant_id``, as the m83 checks require), is managed only by company administrators and
reaches the organizations designated for it (``tenants.settings.credential_orgs``),
projected as ``allowed_org`` + ``consumer`` fanout grants outside legacy mode. Values are
never returned. Personal-account credential types (definition ``personal``, 10.4) are not
shareable at company level.
"""

from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, company_tier, with_designation
from app.authz.error_codes import authorization_error
from app.authz.resource_lifecycle import on_resource_created, on_resource_deleted
from app.credentials import tenant_encryption
from app.credentials.allowed_hosts import default_allowed_hosts
from app.credentials.registry import registry
from app.credentials.service import write_audit_log
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.organization import Tenant
from app.schemas.tenant_catalog import (
    TenantCredentialCreate,
    TenantCredentialUpdate,
    TenantCredentialView,
)
from app.services.tenants.catalog_common import (
    TenantCatalogMutation,
    commit_company_change,
    lock_for_write,
    record,
    require_company_orgs,
)
from app.tenancy.admin_context import TenantAdminContext

_SOURCE = "tenant_api"


def credential_view(credential: Credential, tenant: Tenant) -> TenantCredentialView:
    designated = company_tier(tenant).credential_orgs.get(credential.id, frozenset())
    return TenantCredentialView(
        id=credential.id,
        definition_key=credential.definition_key,
        name=credential.name,
        field_keys=tuple(credential.field_keys or ()),
        status=credential.status,
        allowed_org_ids=sorted(designated),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def _company_rows(tenant_id: UUID) -> Select[tuple[Credential]]:
    return select(Credential).where(
        Credential.scope == "tenant",
        Credential.tenant_id == tenant_id,
        Credential.org_id.is_(None),
    )


async def list_company_credentials(
    db: AsyncSession, context: TenantAdminContext
) -> list[TenantCredentialView]:
    rows = await db.scalars(
        _company_rows(context.tenant.id).order_by(Credential.created_at.desc(), Credential.id)
    )
    return [credential_view(row, context.tenant) for row in rows]


async def _company_credential(db: AsyncSession, tenant: Tenant, credential_id: UUID) -> Credential:
    found = await db.scalar(
        _company_rows(tenant.id).where(Credential.id == credential_id).with_for_update()
    )
    if found is None:
        raise authorization_error("not_found")
    return found


def _require_definition(key: str) -> None:
    definition = registry.get(key)
    if definition is None:
        raise AppError(code="validation_error", message=f"unknown definition '{key}'", status=422)
    if getattr(definition, "personal", False):
        raise AppError(
            code="validation_error",
            message="personal account credentials cannot be shared by the company",
            status=422,
        )


async def create_company_credential(
    mutation: TenantCatalogMutation, tenant_id: UUID, payload: TenantCredentialCreate
) -> TenantCredentialView:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    _require_definition(payload.definition_key)
    orgs = await require_company_orgs(mutation.db, tenant.id, payload.allowed_org_ids)
    credential = Credential(
        id=uuid4(),
        user_id=None,
        org_id=None,
        tenant_id=tenant.id,
        scope="tenant",
        definition_key=payload.definition_key,
        allowed_hosts=default_allowed_hosts(payload.definition_key),
        name=payload.name,
        is_shared=False,
        is_system=True,
        status="active",
    )
    await tenant_encryption.seal(credential, dict(payload.data), db=mutation.db)
    mutation.db.add(credential)
    await mutation.db.flush()
    await write_audit_log(
        mutation.db,
        credential_id=credential.id,
        actor_user_id=mutation.actor.id,
        action="create",
        source=_SOURCE,
        metadata={"definition_key": credential.definition_key},
    )
    created = await on_resource_created(mutation.db, credential, mode=mutation.authz.mode)
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, orgs)
    await _audit(mutation, tenant, credential, audit_actions.TENANT_CREDENTIAL_CREATE, orgs)
    view = credential_view(credential, tenant)
    await commit_company_change(mutation, tenant, "credential", credential.id, created)
    return view


async def update_company_credential(
    mutation: TenantCatalogMutation,
    tenant_id: UUID,
    credential_id: UUID,
    payload: TenantCredentialUpdate,
) -> TenantCredentialView:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    credential = await _company_credential(mutation.db, tenant, credential_id)
    changed = sorted(payload.model_fields_set)
    if payload.name is not None:
        credential.name = payload.name
    if payload.status is not None:
        credential.status = payload.status
    if payload.data is not None:
        await tenant_encryption.seal(credential, dict(payload.data), db=mutation.db)
    orgs = None
    if "allowed_org_ids" in payload.model_fields_set:
        orgs = await require_company_orgs(mutation.db, tenant.id, payload.allowed_org_ids or [])
        tenant.settings = with_designation(
            tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, orgs
        )
    await write_audit_log(
        mutation.db,
        credential_id=credential.id,
        actor_user_id=mutation.actor.id,
        action="update",
        source=_SOURCE,
        metadata={"changed_fields": changed},
    )
    await _audit(mutation, tenant, credential, audit_actions.TENANT_CREDENTIAL_UPDATE, orgs)
    await mutation.db.flush()  # stamps updated_at for the view
    view = credential_view(credential, tenant)
    await commit_company_change(mutation, tenant, "credential", credential.id)
    return view


async def delete_company_credential(
    mutation: TenantCatalogMutation, tenant_id: UUID, credential_id: UUID
) -> None:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    credential = await _company_credential(mutation.db, tenant, credential_id)
    deleted = await on_resource_deleted(mutation.db, credential, mode=mutation.authz.mode)
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, None)
    await _audit(mutation, tenant, credential, audit_actions.TENANT_CREDENTIAL_DELETE, None)
    await mutation.db.delete(credential)
    # The delete hook already retired the row's grants; the re-derive then finds none.
    await commit_company_change(mutation, tenant, "credential", credential.id, deleted)


async def _audit(
    mutation: TenantCatalogMutation,
    tenant: Tenant,
    credential: Credential,
    action: str,
    orgs: list[UUID] | None,
) -> None:
    metadata = {
        "definition_key": credential.definition_key,
        "allowed_org_ids": None if orgs is None else [str(org) for org in orgs],
    }
    target = ("credential", credential.id, credential.name)
    await record(mutation, tenant, action, target, metadata)
