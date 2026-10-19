"""Company shared credentials ``/api/tenants/{tenant_id}/credentials`` (plan 9.7, S07; E-07).

``can_manage_tenant_catalog`` for every route; values are never returned.
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
    TenantCredentialCreate,
    TenantCredentialUpdate,
    TenantCredentialView,
)
from app.services.tenants.catalog_common import TenantCatalogMutation, require_catalog_manager
from app.services.tenants.company_credentials import (
    create_company_credential,
    delete_company_credential,
    list_company_credentials,
    update_company_credential,
)
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}/credentials", tags=["company administration"])


@router.get("", response_model=list[TenantCredentialView])
@authz_policy(GOVERNANCE)
async def get_company_credentials(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> list[TenantCredentialView]:
    require_catalog_manager(context)
    return await list_company_credentials(db, context)


@router.post("", response_model=TenantCredentialView, status_code=201)
@authz_policy(GOVERNANCE)
async def post_company_credential(
    tenant_id: UUID,
    payload: TenantCredentialCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantCredentialView:
    mutation = TenantCatalogMutation(db, request, user, authz)
    return await create_company_credential(mutation, tenant_id, payload)


@router.patch("/{credential_id}", response_model=TenantCredentialView)
@authz_policy(GOVERNANCE)
async def patch_company_credential(
    tenant_id: UUID,
    credential_id: UUID,
    payload: TenantCredentialUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantCredentialView:
    mutation = TenantCatalogMutation(db, request, user, authz)
    return await update_company_credential(mutation, tenant_id, credential_id, payload)


@router.delete("/{credential_id}", status_code=204)
@authz_policy(GOVERNANCE)
async def delete_company_credential_route(
    tenant_id: UUID,
    credential_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    _context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    mutation = TenantCatalogMutation(db, request, user, authz)
    await delete_company_credential(mutation, tenant_id, credential_id)
    return Response(status_code=204)
