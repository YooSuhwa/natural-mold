"""Company organization directory and lifecycle HTTP boundary."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SELF, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.tenant_organizations import (
    TenantOrganizationAdminPage,
    TenantOrganizationAuditorPage,
    TenantOrganizationCreate,
    TenantOrganizationCreateResult,
    TenantOrganizationRename,
    TenantOrganizationSummary,
)
from app.services.orgs.versioning import etag
from app.services.tenants.organizations import (
    TenantOrganizationMutation,
    create_organization,
    list_organizations,
    rename_organization,
)
from app.services.tenants.pagination import TenantPageQuery
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

router = APIRouter(prefix="/api/tenants/{tenant_id}/organizations", tags=["company administration"])
TenantOrganizationPage = TenantOrganizationAdminPage | TenantOrganizationAuditorPage


async def tenant_context(
    tenant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAdminContext:
    return await load_tenant_admin_context(db, user, TenantAdminTarget(tenant_id))


async def mutation_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> TenantOrganizationMutation:
    return TenantOrganizationMutation(db=db, request=request, expected_version=if_match, actor=user)


@router.get("", response_model=TenantOrganizationPage)
@authz_policy(SELF)
async def get_organizations(
    query: Annotated[TenantPageQuery, Query()],
    context: TenantAdminContext = Depends(tenant_context),
    db: AsyncSession = Depends(get_db),
) -> TenantOrganizationPage:
    return await list_organizations(db, context, query)


@router.post(
    "",
    response_model=TenantOrganizationCreateResult,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_organization(
    payload: TenantOrganizationCreate,
    context: TenantAdminContext = Depends(tenant_context),
    mutation: TenantOrganizationMutation = Depends(mutation_request),
    authz: AuthzService = Depends(get_authz),
) -> JSONResponse:
    result = await create_organization(mutation, context, payload, authz)
    return JSONResponse(
        status_code=201,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(result.organization.updated_at)},
    )


@router.patch(
    "/{org_id}",
    response_model=TenantOrganizationSummary,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def patch_organization(
    org_id: UUID,
    payload: TenantOrganizationRename,
    context: TenantAdminContext = Depends(tenant_context),
    mutation: TenantOrganizationMutation = Depends(mutation_request),
) -> JSONResponse:
    result = await rename_organization(mutation, context, org_id, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
