from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    # M6: connection은 필수. chat_service._resolve_custom_auth가 NULL connection_id
    # CUSTOM tool을 fail-closed로 거부하므로, 생성 시점에 invariant를 강제한다.
    # "인증 없는 공개 API" 사용처도 credential이 비어있는 connection을 명시적으로
    # 바인딩해 의도를 DB에 남긴다.
    connection_id: uuid.UUID


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
    connection_id: uuid.UUID | None = None
    name: str
    description: str | None
    parameters_schema: dict[str, Any] | None
    api_url: str | None
    http_method: str | None
    auth_type: str | None
    tags: list[str] | None = None
    agent_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


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
