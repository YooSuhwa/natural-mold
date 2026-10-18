"""Company member status and full tenant-role administration HTTP boundary."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.membership_apply import apply_for_request
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.tenant_members import (
    TenantMemberAdd,
    TenantMemberAdminView,
    TenantMemberPage,
    TenantMemberStatusPatch,
    TenantRolePage,
    TenantRoleReplacement,
    TenantRoleView,
)
from app.services.tenants.member_write import TenantMemberWrite
from app.services.tenants.members import (
    add_member,
    list_members,
    remove_member,
    set_member_status,
)
from app.services.tenants.pagination import TenantPageQuery
from app.services.tenants.roles import list_roles, replace_roles
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["company administration"])


async def read_context(
    tenant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAdminContext:
    return await load_tenant_admin_context(db, user, TenantAdminTarget(tenant_id))


async def mutation_request(
    tenant_id: UUID,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> TenantMemberWrite:
    return TenantMemberWrite(db, tenant_id, user, request, if_match)


@router.get("/members", response_model=TenantMemberPage)
@authz_policy(SELF)
async def get_members(
    query: Annotated[TenantPageQuery, Query()],
    response: Response,
    context: TenantAdminContext = Depends(read_context),
    db: AsyncSession = Depends(get_db),
) -> TenantMemberPage:
    result = await list_members(db, context, query)
    response.headers["ETag"] = etag(result.updated_at)
    return result.page


@router.post(
    "/members",
    response_model=TenantMemberAdminView,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_member(
    payload: TenantMemberAdd,
    mutation: TenantMemberWrite = Depends(mutation_request),
) -> JSONResponse:
    result = await add_member(mutation, payload.user_id)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        status_code=201,
        content=result.view.model_dump(mode="json"),
        headers={"ETag": etag(result.updated_at)},
    )


@router.patch(
    "/members/{user_id}",
    response_model=TenantMemberAdminView,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def patch_member(
    user_id: UUID,
    payload: TenantMemberStatusPatch,
    mutation: TenantMemberWrite = Depends(mutation_request),
) -> JSONResponse:
    result = await set_member_status(mutation, user_id, payload.status)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        content=result.view.model_dump(mode="json"),
        headers={"ETag": etag(result.updated_at)},
    )


@router.delete("/members/{user_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def delete_member(
    user_id: UUID,
    mutation: TenantMemberWrite = Depends(mutation_request),
) -> Response:
    updated_at = await remove_member(mutation, user_id)
    await apply_for_request(mutation.request, mutation.db)
    return Response(status_code=204, headers={"ETag": etag(updated_at)})


@router.get("/roles", response_model=TenantRolePage)
@authz_policy(SELF)
async def get_roles(
    query: Annotated[TenantPageQuery, Query()],
    response: Response,
    context: TenantAdminContext = Depends(read_context),
    db: AsyncSession = Depends(get_db),
) -> TenantRolePage:
    result = await list_roles(db, context, query)
    response.headers["ETag"] = etag(result.updated_at)
    return result.page


@router.put(
    "/roles/{user_id}",
    response_model=TenantRoleView,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def put_roles(
    user_id: UUID,
    payload: TenantRoleReplacement,
    mutation: TenantMemberWrite = Depends(mutation_request),
) -> JSONResponse:
    result = await replace_roles(mutation, user_id, payload.roles)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        content=result.view.model_dump(mode="json"),
        headers={"ETag": etag(result.updated_at)},
    )
