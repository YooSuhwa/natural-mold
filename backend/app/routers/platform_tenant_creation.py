"""SaaS operator company creation HTTP boundary."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SUPER, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_db, require_saas_platform_operator, verify_csrf
from app.schemas.admin_tenant import PlatformTenantCreate, PlatformTenantCreateResult
from app.services.platform.tenant_creation import PlatformTenantCreation, create_platform_tenant

router = APIRouter(prefix="/api/admin/tenants", tags=["platform company administration"])


async def creation_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_saas_platform_operator),
) -> PlatformTenantCreation:
    return PlatformTenantCreation(db, request, actor)


@router.post(
    "",
    response_model=PlatformTenantCreateResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def create_company(
    payload: PlatformTenantCreate,
    context: PlatformTenantCreation = Depends(creation_context),
    authz: AuthzService = Depends(get_authz),
) -> PlatformTenantCreateResult:
    return await create_platform_tenant(context, payload, authz)
