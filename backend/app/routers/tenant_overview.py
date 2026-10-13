"""Company overview and settings HTTP boundary."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.tenant_overview import (
    TenantAdminOverview,
    TenantAuditorOverview,
    TenantOverview,
    TenantOverviewPatch,
)
from app.services.tenants.overview import (
    TenantOverviewMutation,
    get_tenant_overview,
    update_tenant_overview,
)
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

router = APIRouter(prefix="/api/tenants", tags=["company administration"])
TenantOverviewResponse = TenantAdminOverview | TenantAuditorOverview | TenantOverview


async def tenant_read_context(
    tenant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAdminContext:
    return await load_tenant_admin_context(db, user, TenantAdminTarget(tenant_id))


async def tenant_write_context(
    tenant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAdminContext:
    return await load_tenant_admin_context(
        db, user, TenantAdminTarget(tenant_id, permission="can_manage_tenant")
    )


async def mutation_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> TenantOverviewMutation:
    return TenantOverviewMutation(db=db, request=request, expected_version=if_match)


@router.get("/{tenant_id}", response_model=TenantOverviewResponse)
@authz_policy(SELF)
async def get_company_overview(
    response: Response,
    context: TenantAdminContext = Depends(tenant_read_context),
    db: AsyncSession = Depends(get_db),
) -> TenantOverviewResponse:
    result = await get_tenant_overview(db, context)
    response.headers["ETag"] = etag(result.updated_at)
    return result


@router.patch(
    "/{tenant_id}",
    response_model=TenantOverviewResponse,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def patch_company_overview(
    payload: TenantOverviewPatch,
    context: TenantAdminContext = Depends(tenant_write_context),
    mutation: TenantOverviewMutation = Depends(mutation_request),
) -> JSONResponse:
    result = await update_tenant_overview(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
