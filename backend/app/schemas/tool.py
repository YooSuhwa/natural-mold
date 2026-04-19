from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator
from pydantic_core import PydanticCustomError

# Sentinel used by ToolResponse._mask_auth_config — must not be persisted
# back into the database when a client echoes a previous response.
AUTH_CONFIG_MASK = "***"


def _reject_mask_sentinel(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value:
        for v in value.values():
            if isinstance(v, str) and v == AUTH_CONFIG_MASK:
                # PydanticCustomError serializes via app error handler;
                # plain ValueError leaks an unserializable exception object.
                raise PydanticCustomError(
                    "auth_config_mask",
                    "auth_config value is a reserved mask sentinel",
                )
    return value


class ToolType(enum.StrEnum):
    """도구 타입 열거형. DB 컬럼은 String(20) — StrEnum은 str 호환."""

    BUILTIN = "builtin"
    PREBUILT = "prebuilt"
    CUSTOM = "custom"
    MCP = "mcp"


class ToolCustomCreate(BaseModel):
    name: str
    description: str | None = None
    api_url: str
    http_method: str = "GET"
    parameters_schema: dict[str, Any] | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None


class ToolAuthConfigUpdate(BaseModel):
    auth_config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None

    _validate_auth_config = field_validator("auth_config")(_reject_mask_sentinel)


class MCPServerCreate(BaseModel):
    name: str
    url: str
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None


class ToolResponse(BaseModel):
    id: uuid.UUID
    type: str
    provider_name: str | None = None
    is_system: bool
    mcp_server_id: uuid.UUID | None
    credential_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    name: str
    description: str | None
    parameters_schema: dict[str, Any] | None
    api_url: str | None
    http_method: str | None
    auth_type: str | None
    auth_config: dict[str, Any] | None = None
    tags: list[str] | None = None
    agent_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("auth_config")
    def _mask_auth_config(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        # Mask string values to avoid leaking legacy plaintext secrets via the
        # API response. Keys are preserved so the UI can still detect "configured"
        # state via key presence; the sentinel is non-empty so existing length
        # checks keep working. Round-trip is blocked by _reject_mask_sentinel.
        if not value:
            return value
        return {
            k: (AUTH_CONFIG_MASK if isinstance(v, str) and v else v)
            for k, v in value.items()
        }


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    auth_type: str
    credential_id: uuid.UUID | None = None
    status: str
    tools: list[ToolResponse]
    created_at: datetime

    model_config = {"from_attributes": True}


class CredentialBrief(BaseModel):
    id: uuid.UUID
    name: str
    provider_name: str

    model_config = {"from_attributes": True}


class MCPServerListItem(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    auth_type: str
    credential_id: uuid.UUID | None = None
    credential: CredentialBrief | None = None
    status: str
    tool_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MCPServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    credential_id: uuid.UUID | None = None
    auth_config: dict[str, Any] | None = None

    _validate_auth_config = field_validator("auth_config")(_reject_mask_sentinel)
