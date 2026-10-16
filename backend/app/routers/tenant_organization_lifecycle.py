"""Company organization archive/restore HTTP boundary.

Not globally registered here: parent Task 14 alone edits ``router_registry``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.tenant_organizations import mutation_request, tenant_context
from app.schemas.tenant_organization_lifecycle import TenantOrganizationArchive
from app.schemas.tenant_organizations import TenantOrganizationSummary
from app.services.orgs.versioning import etag
from app.services.tenants.organization_lifecycle import (
    archive_organization,
    restore_organization,
)
from app.services.tenants.organizations import TenantOrganizationMutation
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/organizations/{org_id}", tags=["company administration"]
)


@router.post(
    "/archive",
    response_model=TenantOrganizationSummary,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_organization_archive(
    org_id: UUID,
    payload: TenantOrganizationArchive,
    context: TenantAdminContext = Depends(tenant_context),
    mutation: TenantOrganizationMutation = Depends(mutation_request),
) -> JSONResponse:
    result = await archive_organization(mutation, context, org_id, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.post(
    "/restore",
    response_model=TenantOrganizationSummary,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_organization_restore(
    org_id: UUID,
    context: TenantAdminContext = Depends(tenant_context),
    mutation: TenantOrganizationMutation = Depends(mutation_request),
) -> JSONResponse:
    result = await restore_organization(mutation, context, org_id)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
