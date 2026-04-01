from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ToolCustomCreate(BaseModel):
    name: str
    description: str | None = None
    api_url: str
    http_method: str = "GET"
    parameters_schema: dict[str, Any] | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None


class MCPServerCreate(BaseModel):
    name: str
    url: str
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None


class ToolResponse(BaseModel):
    id: uuid.UUID
    type: str
    mcp_server_id: uuid.UUID | None
    name: str
    description: str | None
    parameters_schema: dict[str, Any] | None
    api_url: str | None
    http_method: str | None
    auth_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    auth_type: str
    status: str
    tools: list[ToolResponse]
    created_at: datetime

    model_config = {"from_attributes": True}
