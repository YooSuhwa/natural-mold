"""SaaS operator company directory HTTP boundary."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SUPER, authz_policy
from app.dependencies import CurrentUser, get_db, require_saas_platform_operator, verify_csrf
from app.schemas.admin_tenant import AdminTenantDetail, PlatformTenantMetadataPatch
from app.schemas.tenant_overview import TenantStatus
from app.services.platform.tenant_directory import (
    PlatformTenantDirectoryQuery,
    PlatformTenantMutation,
    PlatformTenantPage,
    get_platform_tenant,
    list_platform_tenants,
    update_platform_tenant,
)
from app.services.tenants.pagination import TenantPageQuery
from app.services.tenants.versioning import etag

router = APIRouter(prefix="/api/admin/tenants", tags=["platform company administration"])


class PlatformTenantQueryParams(TenantPageQuery):
    status: TenantStatus | None = None
    search: str | None = Field(default=None, max_length=200)


@dataclass(frozen=True, slots=True)
class PlatformRequestContext:
    db: AsyncSession
    actor: CurrentUser


async def platform_context(
    db: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_saas_platform_operator),
) -> PlatformRequestContext:
    return PlatformRequestContext(db, actor)


async def platform_mutation(
    tenant_id: UUID,
    request: Request,
    context: PlatformRequestContext = Depends(platform_context),
) -> PlatformTenantMutation:
    return PlatformTenantMutation(
        context.db,
        request,
        context.actor,
        tenant_id,
        request.headers.get("if-match"),
    )


@router.get("", response_model=PlatformTenantPage)
@authz_policy(SUPER)
async def list_companies(
    query: Annotated[PlatformTenantQueryParams, Query()],
    context: PlatformRequestContext = Depends(platform_context),
) -> PlatformTenantPage:
    return await list_platform_tenants(
        context.db,
        PlatformTenantDirectoryQuery(
            status=query.status,
            search=query.search,
            cursor=query.cursor,
            limit=query.limit,
        ),
    )


@router.get("/{tenant_id}", response_model=AdminTenantDetail)
@authz_policy(SUPER)
async def get_company(
    tenant_id: UUID,
    response: Response,
    context: PlatformRequestContext = Depends(platform_context),
) -> AdminTenantDetail:
    result = await get_platform_tenant(context.db, tenant_id)
    response.headers["ETag"] = etag(result.updated_at)
    return result


@router.patch(
    "/{tenant_id}",
    response_model=AdminTenantDetail,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def patch_company(
    payload: PlatformTenantMetadataPatch,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> JSONResponse:
    result = await update_platform_tenant(mutation, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
