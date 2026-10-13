"""Company invitation responses exclude hashes and secret material."""

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

InvitationRole = Literal["admin", "auditor"]
InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class TenantInvitationCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: EmailStr
    roles: tuple[InvitationRole, ...] = Field(default=(), max_length=1)


class TenantInvitationRecord(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def email(self) -> str: ...

    @property
    def role(self) -> InvitationRole | None: ...

    @property
    def expires_at(self) -> datetime: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def status(self) -> InvitationStatus: ...


class TenantInvitationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    email: EmailStr
    role: InvitationRole | None
    expires_at: datetime
    created_at: datetime
    status: InvitationStatus

    @classmethod
    def from_record(cls, record: TenantInvitationRecord) -> "TenantInvitationView":
        return cls(
            id=record.id,
            email=record.email,
            role=record.role,
            expires_at=record.expires_at,
            created_at=record.created_at,
            status=record.status,
        )


class TenantInvitationCreated(TenantInvitationView):
    token: str
