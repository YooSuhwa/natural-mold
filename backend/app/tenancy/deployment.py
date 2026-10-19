"""Mode changes affect management exposure, never implicit company membership."""

from typing import Literal

from app.authz.org_context import OrganizationActor
from app.exceptions import AppError

DeploymentMode = Literal["single_tenant", "multi_tenant"]


def platform_tenant_management_enabled(actor: OrganizationActor, mode: DeploymentMode) -> bool:
    return mode == "multi_tenant" and actor.is_super_user


def require_platform_tenant_management(actor: OrganizationActor, mode: DeploymentMode) -> None:
    if mode != "multi_tenant":
        raise AppError(code="not_found", message="요청한 경로를 찾을 수 없습니다", status=404)
    if not actor.is_super_user:
        raise AppError(code="forbidden", message="운영자 권한이 필요합니다", status=403)
