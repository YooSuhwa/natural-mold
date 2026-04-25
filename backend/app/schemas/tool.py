from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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


class ToolResponse(BaseModel):
    id: uuid.UUID
    type: str
    provider_name: str | None = None
    is_system: bool
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


class ToolUpdate(BaseModel):
    """PATCH /api/tools/{id} payload — connection_id 단일 필드 (M6.1 옵션 D).

    `extra="forbid"`로 알 수 없는 필드는 422로 거부 (스코프 보호).
    명시적 None 전송 시 connection 해제로 해석된다.
    """

    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID | None = None


class DiscoverToolItem(BaseModel):
    tool: ToolResponse
    status: str  # "created" | "existing"


class DiscoverToolsResponse(BaseModel):
    connection_id: uuid.UUID
    server_info: dict[str, Any] = {}
    items: list[DiscoverToolItem]
