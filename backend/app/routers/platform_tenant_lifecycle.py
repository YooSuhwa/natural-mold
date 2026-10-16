"""SaaS operator company suspend/resume HTTP boundary.

Not globally registered here: parent Task 14 alone edits ``router_registry``.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.authz.policies import SUPER, authz_policy
from app.dependencies import verify_csrf
from app.routers.platform_tenant_directory import platform_mutation
from app.schemas.admin_tenant import AdminTenantDetail
from app.schemas.platform_tenant_lifecycle import PlatformTenantLifecycleRequest
from app.services.platform.tenant_directory import PlatformTenantMutation
from app.services.platform.tenant_lifecycle import change_platform_tenant_status
from app.services.tenants.versioning import etag

router = APIRouter(prefix="/api/admin/tenants", tags=["platform company administration"])


def _reason(payload: PlatformTenantLifecycleRequest | None) -> str | None:
    return None if payload is None else payload.reason


def _response(result: AdminTenantDetail) -> JSONResponse:
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.post(
    "/{tenant_id}/suspend",
    response_model=AdminTenantDetail,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def suspend_company(
    payload: PlatformTenantLifecycleRequest | None = None,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> JSONResponse:
    return _response(await change_platform_tenant_status(mutation, "suspend", _reason(payload)))


@router.post(
    "/{tenant_id}/resume",
    response_model=AdminTenantDetail,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def resume_company(
    payload: PlatformTenantLifecycleRequest | None = None,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> JSONResponse:
    return _response(await change_platform_tenant_status(mutation, "resume", _reason(payload)))
