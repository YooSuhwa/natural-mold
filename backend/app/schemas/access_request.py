"""Access request contracts (plan 9.7 "접근 요청", 9.7.1, 10.9, S18; H-01)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RequestableType = Literal["agent", "skill", "tool", "mcp_server"]
RequestLevel = Literal["consumer", "viewer", "editor"]  # "manager" only by the owner (S18)
RequestStatus = Literal["pending", "approved", "denied", "cancelled", "expired"]
RequestBox = Literal["inbox", "outbox", "handled"]


class AccessRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: RequestableType
    resource_id: UUID
    level: RequestLevel
    reason: str | None = Field(default=None, max_length=500)


class AccessRequestApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: RequestLevel | None = None  # lower than requested only; default: as requested
    expires_at: datetime | None = None  # agent ``consumer`` only (10.2)
    confirm_delegation: bool = False  # D17, as on a share
    note: str | None = Field(default=None, max_length=500)


class AccessRequestDeny(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class RequestUser(BaseModel):
    id: UUID
    display_name: str | None = None


class AccessRequestView(BaseModel):
    id: UUID
    resource_type: RequestableType
    resource_id: UUID
    resource_name: str | None
    level: RequestLevel
    reason: str | None
    status: RequestStatus
    requester: RequestUser
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    granted_level: RequestLevel | None
    created_at: datetime
    expires_at: datetime


class AccessRequestPage(BaseModel):
    items: list[AccessRequestView]
    next_cursor: str | None = None
    has_more: bool = False
