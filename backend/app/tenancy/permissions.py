"""Company governance permissions; these never authorize resource contents."""

from typing import Final, Literal

from app.exceptions import AppError
from app.tenancy.context import TenantContext

TenantPermission = Literal[
    "can_manage_tenant",
    "can_create_org",
    "can_manage_tenant_catalog",
    "can_view_tenant_usage",
    "can_view_tenant_audit",
]
AUDITOR_PERMISSIONS: Final[tuple[TenantPermission, ...]] = (
    "can_view_tenant_usage",
    "can_view_tenant_audit",
)
ADMIN_PERMISSIONS: Final[tuple[TenantPermission, ...]] = (
    "can_manage_tenant",
    "can_create_org",
    "can_manage_tenant_catalog",
    *AUDITOR_PERMISSIONS,
)


def tenant_permissions(context: TenantContext) -> tuple[TenantPermission, ...]:
    if "admin" in context.roles:
        return ADMIN_PERMISSIONS
    if "auditor" in context.roles:
        return AUDITOR_PERMISSIONS
    return ()


def require_tenant_permission(context: TenantContext, permission: TenantPermission) -> None:
    if permission not in tenant_permissions(context):
        raise AppError(
            code="permission_required", message="회사 관리 권한이 필요합니다", status=403
        )
