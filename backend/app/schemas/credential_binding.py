"""Per-user connection and credential mode payloads (plan 9.x ``/api/credential-bindings``,
S28 credential mode; F-04)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BindingResourceType = Literal["tool", "mcp_server"]
CredentialMode = Literal["none", "shared", "per_user"]


class CredentialBindingIn(BaseModel):
    credential_id: uuid.UUID


class CredentialBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_type: BindingResourceType
    resource_id: uuid.UUID
    credential_id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CredentialModeIn(BaseModel):
    credential_mode: CredentialMode
    # The type each caller must connect in ``per_user`` mode; default: the current
    # credential's type (tools also accept their definition's types when unset).
    credential_definition_key: str | None = Field(default=None, max_length=80)


class CredentialModeOut(BaseModel):
    resource_type: BindingResourceType
    resource_id: uuid.UUID
    credential_mode: CredentialMode
    credential_definition_key: str | None = None
    # Callers already connected (kept across mode switches; used again on per_user).
    binding_count: int = 0


__all__ = [
    "BindingResourceType",
    "CredentialBindingIn",
    "CredentialBindingOut",
    "CredentialMode",
    "CredentialModeIn",
    "CredentialModeOut",
]
