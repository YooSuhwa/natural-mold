from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator


class ToolType(enum.StrEnum):
    """도구 타입 열거형. DB 컬럼은 String(20) — StrEnum은 str 호환."""

    BUILTIN = "builtin"
    PREBUILT = "prebuilt"
    CUSTOM = "custom"
    MCP = "mcp"


def _check_server_key_available(name: str) -> bool:
    """Check if server-level API keys are configured in .env for a prebuilt tool."""
    from app.config import settings

    low = name.lower()
    if low.startswith("naver"):
        return bool(settings.naver_client_id and settings.naver_client_secret)
    if low.startswith("google") and "chat" not in low:
        return bool(settings.google_api_key and settings.google_cse_id)
    if "chat send" in low:
        return bool(settings.google_chat_webhook_url)
    if "gmail" in low or "calendar" in low:
        return bool(
            settings.google_oauth_client_id
            and settings.google_oauth_client_secret
            and settings.google_oauth_refresh_token
        )
    return False


class ToolCustomCreate(BaseModel):
    name: str
    description: str | None = None
    api_url: str
    http_method: str = "GET"
    parameters_schema: dict[str, Any] | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None


class ToolAuthConfigUpdate(BaseModel):
    auth_config: dict[str, Any]


class MCPServerCreate(BaseModel):
    name: str
    url: str
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None


class ToolResponse(BaseModel):
    id: uuid.UUID
    type: str
    is_system: bool
    mcp_server_id: uuid.UUID | None
    name: str
    description: str | None
    parameters_schema: dict[str, Any] | None
    api_url: str | None
    http_method: str | None
    auth_type: str | None
    auth_config: dict[str, Any] | None = None
    tags: list[str] | None = None
    server_key_available: bool = False
    agent_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def compute_server_key_available(self) -> ToolResponse:
        if self.type == ToolType.PREBUILT:
            self.server_key_available = _check_server_key_available(self.name)
        return self


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    auth_type: str
    status: str
    tools: list[ToolResponse]
    created_at: datetime

    model_config = {"from_attributes": True}
