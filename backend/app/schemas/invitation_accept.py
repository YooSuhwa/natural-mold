"""Invitation tokens are write-only secrets; acceptance returns only the selected scope."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class InvitationAccept(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=256)


class InvitationAccepted(BaseModel):
    model_config = ConfigDict(frozen=True)
    org_id: UUID
    tenant_id: UUID
    user_id: UUID
