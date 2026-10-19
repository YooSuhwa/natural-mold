"""Company catalog policy and service LLM HTTP boundary (plan 9.7, S07; E-07).

``can_manage_tenant_catalog`` (company administrator) for reads and writes; a foreign or
missing company is the same 404 as on the other company routes.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_policies import company_context
from app.schemas.org_catalog import CatalogPolicy
from app.schemas.tenant_catalog import (
    TenantCatalogPolicyView,
    TenantLlmSettingsView,
    TenantLlmSettingUpdate,
)
from app.services.tenants.catalog_common import TenantCatalogMutation, require_catalog_manager
from app.services.tenants.company_catalog_policy import (
    read_company_catalog,
    update_company_catalog,
)
from app.services.tenants.company_llm_settings import read_llm_settings, update_llm_setting
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["company administration"])


@router.get("/catalog-policy", response_model=TenantCatalogPolicyView)
@authz_policy(GOVERNANCE)
async def get_company_catalog_policy(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> JSONResponse:
    require_catalog_manager(context)
    view = await read_company_catalog(db, context)
    return JSONResponse(view.model_dump(mode="json"), headers={"ETag": etag(view.updated_at)})


@router.put("/catalog-policy", response_model=TenantCatalogPolicyView)
@authz_policy(GOVERNANCE)
async def put_company_catalog_policy(
    tenant_id: UUID,
    payload: CatalogPolicy,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    mutation = TenantCatalogMutation(db, request, user, authz)
    view = await update_company_catalog(mutation, tenant_id, payload, if_match)
    return JSONResponse(view.model_dump(mode="json"), headers={"ETag": etag(view.updated_at)})


@router.get("/llm-settings", response_model=TenantLlmSettingsView)
@authz_policy(GOVERNANCE)
async def get_company_llm_settings(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> TenantLlmSettingsView:
    require_catalog_manager(context)
    return await read_llm_settings(db, context)


@router.put("/llm-settings", response_model=TenantLlmSettingsView)
@authz_policy(GOVERNANCE)
async def put_company_llm_setting(
    tenant_id: UUID,
    payload: TenantLlmSettingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantLlmSettingsView:
    mutation = TenantCatalogMutation(db, request, user, authz)
    return await update_llm_setting(mutation, tenant_id, payload)
