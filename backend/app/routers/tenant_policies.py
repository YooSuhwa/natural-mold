"""``GET|PUT /api/tenants/{tenant_id}/policies`` HTTP boundary (plan 9.7, S05; E-09)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.tenant_publication import TenantPolicies, TenantPoliciesUpdate
from app.services.tenants.policies import (
    TenantPolicyMutation,
    tenant_policies,
    update_tenant_policies,
)
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

router = APIRouter(prefix="/api/tenants/{tenant_id}/policies", tags=["company administration"])


async def company_context(
    tenant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAdminContext:
    """Company members only; a foreign or missing company is the same 404."""
    return await load_tenant_admin_context(db, user, TenantAdminTarget(tenant_id))


@router.get("", response_model=TenantPolicies)
@authz_policy(SELF)
async def get_tenant_policies(
    response: Response, context: TenantAdminContext = Depends(company_context)
) -> TenantPolicies:
    result = tenant_policies(context.tenant)
    response.headers["ETag"] = etag(result.updated_at)
    return result


@router.put("", response_model=TenantPolicies)
@authz_policy(GOVERNANCE)
async def put_tenant_policies(
    payload: TenantPoliciesUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    mutation = TenantPolicyMutation(db=db, request=request, expected_version=if_match, actor=user)
    result = await update_tenant_policies(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
