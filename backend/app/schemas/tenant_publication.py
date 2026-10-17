"""Company publication policy and approval queue models (plan 9.7, 10.2, S05; E-09)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.retention import AuditRetentionDays, RetentionPreview

TenantPublishPolicy = Literal["disabled", "open", "approval"]
TenantPublicationStatus = Literal["none", "pending", "approved", "rejected"]
RejectionNote = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class TenantPolicies(BaseModel):
    """``GET /api/tenants/{tenant_id}/policies`` (company members may read)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    marketplace_tenant_publish: TenantPublishPolicy
    audit_retention_days: int  # E-08, 14.1 (default 365)
    updated_at: datetime
    # ``PUT`` only: a shortened audit retention was not saved; resend with ``confirm``.
    preview: RetentionPreview | None = None


class TenantPoliciesUpdate(BaseModel):
    """An omitted ``audit_retention_days`` keeps its value; shortening it needs ``confirm``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    marketplace_tenant_publish: TenantPublishPolicy
    audit_retention_days: AuditRetentionDays | None = None
    confirm: bool = False


class TenantPublicationState(BaseModel):
    """The item's company publication state, returned to the publisher."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID
    tenant_publication: TenantPublicationStatus
    requested_at: datetime | None
    reviewed_at: datetime | None
    note: str | None


class PublicationRequestOrganization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str


class PublicationRequestPublisher(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID | None
    name: str | None


class PublicationRequestItem(BaseModel):
    """What an approver may see (10.2): never the item's contents (D8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID
    name: str
    description: str | None
    resource_type: str
    organization: PublicationRequestOrganization
    publisher: PublicationRequestPublisher
    requested_at: datetime | None
    credential_types: tuple[str, ...]


class PublicationRequestPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[PublicationRequestItem, ...]
    next_cursor: str | None
    has_more: bool


class PublicationRejection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note: RejectionNote
