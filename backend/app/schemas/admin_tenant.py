"""SaaS operator company metadata excludes customer content."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.organization import Tenant
from app.schemas.tenant_overview import (
    TENANT_STATUS_ADAPTER,
    TenantName,
    TenantStatus,
)
from app.services.tenants.identifiers import TenantSlug


class TenantLimitSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    member_limit: int | None = Field(default=None, ge=0)
    monthly_spend_limit: Decimal | None = Field(default=None, ge=0)
    concurrent_run_limit: int | None = Field(default=None, ge=0)
    allow_system_models: bool = False

    @classmethod
    def from_tenant(cls, tenant: Tenant) -> "TenantLimitSummary":
        return cls.model_validate(
            {
                "member_limit": tenant.limits.get("member_limit"),
                "monthly_spend_limit": tenant.limits.get("monthly_spend_limit"),
                "concurrent_run_limit": tenant.limits.get("concurrent_run_limit"),
                "allow_system_models": tenant.limits.get("allow_system_models", False),
            }
        )


class PlatformTenantCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TenantName
    slug: TenantSlug
    first_admin_email: EmailStr
    first_organization_name: TenantName
    member_limit: int | None = Field(default=None, ge=0)
    monthly_spend_limit: Decimal | None = Field(default=None, ge=0)
    concurrent_run_limit: int | None = Field(default=None, ge=0)
    allow_system_models: bool = False


class PlatformTenantMetadataPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TenantName | None = None
    member_limit: int | None = Field(default=None, ge=0)
    monthly_spend_limit: Decimal | None = Field(default=None, ge=0)
    concurrent_run_limit: int | None = Field(default=None, ge=0)
    allow_system_models: bool = False


class AdminTenantMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
    slug: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    limits: TenantLimitSummary

    @classmethod
    def from_tenant(cls, tenant: Tenant) -> "AdminTenantMetadata":
        return cls(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=TENANT_STATUS_ADAPTER.validate_python(tenant.status),
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            limits=TenantLimitSummary.from_tenant(tenant),
        )


class AdminTenantDetail(AdminTenantMetadata):
    organization_count: int
    active_member_count: int
