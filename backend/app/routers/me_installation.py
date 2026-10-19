"""Expose installation navigation flags without requiring a company membership."""

from fastapi import APIRouter, Depends

from app.authz.policies import SELF, authz_policy
from app.config import settings
from app.dependencies import CurrentUser, get_current_user
from app.schemas.installation import InstallationResponse
from app.tenancy.deployment import platform_tenant_management_enabled

router = APIRouter(prefix="/api/me", tags=["installation"])


@router.get("/installation", response_model=InstallationResponse)
@authz_policy(SELF)
async def get_installation(
    user: CurrentUser = Depends(get_current_user),
) -> InstallationResponse:
    return InstallationResponse(
        deployment_mode=settings.deployment_mode,
        org_multi_enabled=settings.org_multi_enabled,
        platform_tenant_management_enabled=platform_tenant_management_enabled(
            user, settings.deployment_mode
        ),
    )
