"""S30 company domains and SSO policy (plan 9.7, 10.10; H-08). ``can_manage_tenant``.

``/api/tenants/{tenant_id}/domains`` CRUD + ``/{id}/verify`` and
``GET|PUT /api/tenants/{tenant_id}/sso-policy``. A foreign or missing company is 404,
a company member without ``can_manage_tenant`` 403 ``permission_required``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_policies import company_context
from app.schemas.sso import (
    SsoPolicyRead,
    SsoPolicyUpdate,
    TenantDomainCreate,
    TenantDomainList,
    TenantDomainRead,
)
from app.services.sso import domains, policy
from app.services.sso.common import SsoMutation, require_manage
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["company sso"])


async def manager_context(
    context: TenantAdminContext = Depends(company_context),
) -> TenantAdminContext:
    require_manage(context)
    return context


@router.get("/domains", response_model=TenantDomainList)
@authz_policy(GOVERNANCE)
async def list_tenant_domains(
    db: AsyncSession = Depends(get_db), context: TenantAdminContext = Depends(manager_context)
) -> TenantDomainList:
    return await domains.list_domains(db, context.tenant.id)


@router.post("/domains", response_model=TenantDomainRead, status_code=201)
@authz_policy(GOVERNANCE)
async def add_tenant_domain(
    payload: TenantDomainCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> TenantDomainRead:
    return await domains.add_domain(SsoMutation(db, request, user), context, payload)


@router.post("/domains/{domain_id}/verify", response_model=TenantDomainRead)
@authz_policy(GOVERNANCE)
async def verify_tenant_domain(
    domain_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> TenantDomainRead:
    return await domains.verify_domain(SsoMutation(db, request, user), context, domain_id)


@router.delete("/domains/{domain_id}", status_code=204)
@authz_policy(GOVERNANCE)
async def remove_tenant_domain(
    domain_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> Response:
    await domains.remove_domain(SsoMutation(db, request, user), context, domain_id)
    return Response(status_code=204)


@router.get("/sso-policy", response_model=SsoPolicyRead)
@authz_policy(GOVERNANCE)
async def get_tenant_sso_policy(
    db: AsyncSession = Depends(get_db), context: TenantAdminContext = Depends(manager_context)
) -> SsoPolicyRead:
    return await policy.policy_view(db, context.tenant)


@router.put("/sso-policy", response_model=SsoPolicyRead)
@authz_policy(GOVERNANCE)
async def put_tenant_sso_policy(
    payload: SsoPolicyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoPolicyRead:
    return await policy.update_policy(SsoMutation(db, request, user), context, payload)
