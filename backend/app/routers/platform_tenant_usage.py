"""SaaS operator company summary reads for the S03/S04 screens (U-01).

``SUPER`` and SaaS-only like the other ``/api/admin/tenants`` routes (``platform_context``
-> ``require_saas_platform_operator``); neither resolves a company context.

- ``/limits`` reuses the company limit view the company admins read at
  ``/api/tenants/{id}/limits`` (S04 "이번 달 사용량", 13.12.2 "진행 중 실행 수"): amounts and
  counts only, no company content.
- ``/deletion`` reads the I-07 deletion state (S04 "삭제 예정(D-30)") so a reload keeps the
  due date; the directory and detail stay metadata-only (B-12 query boundary).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.authz.policies import SUPER, authz_policy
from app.authz.usage_limit_access import LimitTarget
from app.routers.platform_tenant_directory import PlatformRequestContext, platform_context
from app.schemas.platform_tenant_data import TenantDeletionState
from app.schemas.usage_limits import UsageLimitView
from app.services.platform.tenant_deletion import deletion_state
from app.services.tenants.versioning import etag
from app.tenancy.limit_settings import limit_view

router = APIRouter(prefix="/api/admin/tenants", tags=["platform company administration"])


@router.get("/{tenant_id}/limits", response_model=UsageLimitView)
@authz_policy(SUPER)
async def get_company_limits(
    tenant_id: UUID,
    context: PlatformRequestContext = Depends(platform_context),
) -> UsageLimitView:
    return await limit_view(context.db, LimitTarget("tenant", tenant_id, tenant_id))


@router.get("/{tenant_id}/deletion", response_model=TenantDeletionState)
@authz_policy(SUPER)
async def get_company_deletion(
    tenant_id: UUID,
    response: Response,
    context: PlatformRequestContext = Depends(platform_context),
) -> TenantDeletionState:
    state = await deletion_state(context.db, tenant_id)
    response.headers["ETag"] = etag(state.tenant.updated_at)
    return state
