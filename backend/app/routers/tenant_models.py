"""Company models HTTP boundary ``/api/tenants/{tenant_id}/models`` (plan 9.7, S07; E-07).

``can_manage_tenant_catalog`` for every route. Platform models are listed for narrowing
(organization designation only); company models are fully managed here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_policies import company_context
from app.schemas.tenant_catalog import (
    TenantModelCreate,
    TenantModelList,
    TenantModelUpdate,
    TenantModelView,
)
from app.services.tenants.catalog_common import TenantCatalogMutation, require_catalog_manager
from app.services.tenants.company_models import (
    create_company_model,
    delete_company_model,
    list_company_models,
    update_company_model,
)
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}/models", tags=["company administration"])


@router.get("", response_model=TenantModelList)
@authz_policy(GOVERNANCE)
async def get_company_models(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> TenantModelList:
    require_catalog_manager(context)
    return await list_company_models(db, context)


@router.post("", response_model=TenantModelView, status_code=201)
@authz_policy(GOVERNANCE)
async def post_company_model(
    tenant_id: UUID,
    payload: TenantModelCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantModelView:
    mutation = TenantCatalogMutation(db, request, user, authz)
    return await create_company_model(mutation, tenant_id, payload)


@router.patch("/{model_id}", response_model=TenantModelView)
@authz_policy(GOVERNANCE)
async def patch_company_model(
    tenant_id: UUID,
    model_id: UUID,
    payload: TenantModelUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantModelView:
    mutation = TenantCatalogMutation(db, request, user, authz)
    return await update_company_model(mutation, tenant_id, model_id, payload)


@router.delete("/{model_id}", status_code=204)
@authz_policy(GOVERNANCE)
async def delete_company_model_route(
    tenant_id: UUID,
    model_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    mutation = TenantCatalogMutation(db, request, user, authz)
    await delete_company_model(mutation, tenant_id, model_id)
    return Response(status_code=204)
