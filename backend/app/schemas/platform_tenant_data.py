"""Operator company export and deletion schedule payloads (plan S04, 9.7; I-07)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.admin_tenant import AdminTenantDetail
from app.schemas.platform_tenant_lifecycle import PlatformTenantLifecycleRequest

TenantExportStatus = Literal["queued", "running", "succeeded", "failed"]


class TenantExportJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    status: TenantExportStatus
    progress: int
    error_code: str | None
    size_bytes: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
    download_available: bool


class TenantExportStarted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID


class TenantExportList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[TenantExportJob]


class TenantDeletionRequest(PlatformTenantLifecycleRequest):
    """S04: the operator types the company slug to confirm."""

    confirm_slug: str = Field(min_length=1, max_length=120)


class TenantDeletionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: AdminTenantDetail
    deletion_due_at: datetime | None
