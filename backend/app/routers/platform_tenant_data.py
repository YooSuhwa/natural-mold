"""SaaS operator company export and deletion schedule HTTP boundary (plan S04, 9.7; I-07).

All routes are ``SUPER`` and SaaS-only (``platform_context`` ->
``require_saas_platform_operator``); none resolves a company context. Mutations take
CSRF first and ``If-Match`` (read inside ``platform_mutation``) for the schedule/cancel
transitions, exactly like suspend/resume.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SUPER, authz_policy
from app.dependencies import verify_csrf
from app.routers.platform_tenant_directory import (
    PlatformRequestContext,
    platform_context,
    platform_mutation,
)
from app.schemas.platform_tenant_data import (
    TenantDeletionRequest,
    TenantDeletionState,
    TenantExportList,
    TenantExportStarted,
)
from app.schemas.platform_tenant_lifecycle import PlatformTenantLifecycleRequest
from app.services.platform import tenant_exports
from app.services.platform.tenant_command import TenantCommand
from app.services.platform.tenant_deletion import (
    cancel_tenant_deletion,
    deletion_state,
    schedule_tenant_deletion,
)
from app.services.platform.tenant_directory import PlatformTenantMutation
from app.services.tenants.versioning import etag

router = APIRouter(prefix="/api/admin/tenants", tags=["platform company administration"])


async def _state(db: AsyncSession, tenant_id: UUID) -> JSONResponse:
    state = await deletion_state(db, tenant_id)
    return JSONResponse(
        content=state.model_dump(mode="json"), headers={"ETag": etag(state.tenant.updated_at)}
    )


@router.post(
    "/{tenant_id}/export",
    response_model=TenantExportStarted,
    status_code=202,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def start_company_export(
    background: BackgroundTasks,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> TenantExportStarted:
    job_id = await tenant_exports.start_export(TenantCommand.from_mutation(mutation))
    background.add_task(tenant_exports.run_export, job_id)
    return TenantExportStarted(job_id=job_id)


@router.get("/{tenant_id}/exports", response_model=TenantExportList)
@authz_policy(SUPER)
async def list_company_exports(
    tenant_id: UUID,
    context: PlatformRequestContext = Depends(platform_context),
) -> TenantExportList:
    rows = await tenant_exports.list_exports(context.db, tenant_id)
    now = datetime.now(UTC)
    return TenantExportList(items=[tenant_exports.job_view(row, now) for row in rows])


@router.get("/{tenant_id}/exports/{export_id}/download", response_class=FileResponse)
@authz_policy(SUPER)
async def download_company_export(
    tenant_id: UUID,
    export_id: UUID,
    context: PlatformRequestContext = Depends(platform_context),
) -> FileResponse:
    path = await tenant_exports.export_file(context.db, tenant_id, export_id)
    filename = f"tenant-{tenant_id}-export-{export_id}.zip"
    return FileResponse(path, media_type="application/zip", filename=filename)


@router.post(
    "/{tenant_id}/schedule-deletion",
    response_model=TenantDeletionState,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def schedule_company_deletion(
    payload: TenantDeletionRequest,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> JSONResponse:
    command = TenantCommand.from_mutation(mutation)
    await schedule_tenant_deletion(command, payload.confirm_slug, payload.reason)
    return await _state(mutation.db, mutation.tenant_id)


@router.post(
    "/{tenant_id}/cancel-deletion",
    response_model=TenantDeletionState,
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SUPER)
async def cancel_company_deletion(
    payload: PlatformTenantLifecycleRequest | None = None,
    mutation: PlatformTenantMutation = Depends(platform_mutation),
) -> JSONResponse:
    reason = None if payload is None else payload.reason
    await cancel_tenant_deletion(TenantCommand.from_mutation(mutation), reason)
    return await _state(mutation.db, mutation.tenant_id)
