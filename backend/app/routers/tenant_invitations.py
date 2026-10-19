"""Company invitation list and lifecycle HTTP boundary."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SELF, authz_policy
from app.dependencies import get_db, verify_csrf
from app.routers.tenant_overview import mutation_request, tenant_read_context
from app.schemas.tenant_invitations import (
    TenantInvitationCreate,
    TenantInvitationCreated,
    TenantInvitationPage,
)
from app.services.tenants.invitation_queries import list_invitations
from app.services.tenants.invitations import (
    create_invitation,
    resend_invitation,
    revoke_invitation,
)
from app.services.tenants.overview import TenantOverviewMutation
from app.services.tenants.pagination import TenantPageQuery
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}/invitations", tags=["company administration"])


@router.get("", response_model=TenantInvitationPage)
@authz_policy(SELF)
async def get_invitations(
    query: Annotated[TenantPageQuery, Query()],
    response: Response,
    context: TenantAdminContext = Depends(tenant_read_context),
    db: AsyncSession = Depends(get_db),
) -> TenantInvitationPage:
    result = await list_invitations(db, context, query)
    response.headers["ETag"] = etag(context.tenant.updated_at)
    return result


@router.post(
    "",
    response_model=TenantInvitationCreated,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_invitation(
    payload: TenantInvitationCreate,
    context: TenantAdminContext = Depends(tenant_read_context),
    mutation: TenantOverviewMutation = Depends(mutation_request),
) -> JSONResponse:
    result, created = await create_invitation(mutation, context, payload)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.tenant.updated_at)},
    )


@router.delete(
    "/{invitation_id}",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def delete_invitation(
    invitation_id: UUID,
    context: TenantAdminContext = Depends(tenant_read_context),
    mutation: TenantOverviewMutation = Depends(mutation_request),
) -> Response:
    await revoke_invitation(mutation, context, invitation_id)
    return Response(status_code=204, headers={"ETag": etag(context.tenant.updated_at)})


@router.post(
    "/{invitation_id}/resend",
    response_model=TenantInvitationCreated,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def post_invitation_resend(
    invitation_id: UUID,
    context: TenantAdminContext = Depends(tenant_read_context),
    mutation: TenantOverviewMutation = Depends(mutation_request),
) -> JSONResponse:
    result, created = await resend_invitation(mutation, context, invitation_id)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.tenant.updated_at)},
    )
