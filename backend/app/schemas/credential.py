from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CredentialCreate(BaseModel):
    name: str
    credential_type: str
    provider_name: str
    data: dict[str, str]


class CredentialUpdate(BaseModel):
    name: str | None = None
    data: dict[str, str] | None = None


class CredentialResponse(BaseModel):
    id: uuid.UUID
    name: str
    credential_type: str
    provider_name: str
    is_active: bool
    has_data: bool
    field_keys: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CredentialFieldDef(BaseModel):
    key: str
    label: str
    secret: bool = True
    default: str | None = None


class CredentialProviderDef(BaseModel):
    key: str
    name: str
    credential_type: str
    fields: list[CredentialFieldDef]


class CredentialUsageResponse(BaseModel):
    credential_id: uuid.UUID
    tool_count: int
    mcp_server_count: int
