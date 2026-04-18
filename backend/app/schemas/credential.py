from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

# m10 auto-seed rows의 `name` 필드도 동일 프리픽스를 쓰므로, 사용자가 API로
# 이 프리픽스를 쓰지 못하도록 예약. 순환 import 방지를 위해 경량 markers 모듈에서 로드.
from app.schemas.markers import M10_SEED_MARKER


def _reject_reserved_marker(value: str | None, field: str) -> str | None:
    if value is None:
        return value
    if value.startswith(M10_SEED_MARKER):
        raise ValueError(
            f"{field} cannot start with the reserved marker "
            f"'{M10_SEED_MARKER}' — reserved for m10 auto-seeded rows."
        )
    return value


class CredentialCreate(BaseModel):
    name: str
    credential_type: str
    provider_name: str
    data: dict[str, str]

    @field_validator("name")
    @classmethod
    def _check_name_marker(cls, v: str) -> str:
        return _reject_reserved_marker(v, "name") or v


class CredentialUpdate(BaseModel):
    name: str | None = None
    data: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def _check_name_marker(cls, v: str | None) -> str | None:
        return _reject_reserved_marker(v, "name")


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
