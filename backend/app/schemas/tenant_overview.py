"""Company overview boundary models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter

from app.models.organization import Tenant
from app.schemas.tenant_organizations import TenantOrganizationAuditorSummary
from app.services.tenants.identifiers import TenantSlug
from app.tenancy.admin_context import TenantAccess

TenantStatus = Literal["active", "suspended", "deletion_scheduled"]
TENANT_STATUS_ADAPTER = TypeAdapter(TenantStatus)
TenantName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TenantIdentifierFields(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: TenantSlug


class TenantOverviewPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TenantName


class TenantOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: TenantName
    slug: TenantSlug
    status: TenantStatus
    updated_at: datetime
    access: TenantAccess

    @classmethod
    def from_tenant(cls, tenant: Tenant, *, access: TenantAccess) -> "TenantOverview":
        return cls(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=TENANT_STATUS_ADAPTER.validate_python(tenant.status),
            updated_at=tenant.updated_at,
            access=access,
        )


class TenantAuditorOverview(TenantOverview):
    active_member_count: int
    organization_count: int
    organizations: tuple[TenantOrganizationAuditorSummary, ...]


class TenantAdminOverview(TenantAuditorOverview):
    active_admin_count: int
