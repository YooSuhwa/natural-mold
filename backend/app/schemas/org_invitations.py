"""Invite metadata excludes hashes; only authorized creation returns the link token."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class InvitationCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    email: EmailStr
    role: Literal["member", "builder", "admin", "auditor"] | None = None
    group_ids: tuple[UUID, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def unique_groups(self) -> "InvitationCreate":
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("group_ids must be unique")
        return self


class InvitationView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    email: str
    role: Literal["member", "builder", "admin", "auditor"]
    group_ids: tuple[UUID, ...]
    expires_at: datetime
    created_at: datetime
    status: Literal["pending", "accepted", "revoked", "expired"]


class InvitationCreated(InvitationView):
    token: str | None
