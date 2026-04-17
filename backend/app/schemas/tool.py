from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


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


class ToolAuthConfigUpdate(BaseModel):
    auth_config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None


class MCPServerCreate(BaseModel):
    name: str
    url: str
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None


class ToolResponse(BaseModel):
    id: uuid.UUID
    type: str
    is_system: bool
    mcp_server_id: uuid.UUID | None
    credential_id: uuid.UUID | None = None
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
