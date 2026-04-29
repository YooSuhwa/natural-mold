"""Pydantic schemas for the Tool API.

Greenfield (M3) schemas live first; the legacy ``ToolType`` / ``ToolCustomCreate``
/ legacy ``ToolResponse`` shims at the bottom of the file are retained so the
not-yet-rewired services in ``app/services/tool_service.py`` keep importing —
M5 deletes them along with the old service.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---- Catalog ---------------------------------------------------------------


class ToolFieldSchema(BaseModel):
    name: str
    display_name: str
    kind: str
    default: Any = None
    required: bool = False
    description: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    placeholder: str | None = None
    type_options: dict[str, Any] = Field(default_factory=dict)
    display_options: dict[str, Any] = Field(default_factory=dict)


class ToolDefinitionSchema(BaseModel):
    key: str
    display_name: str
    description: str
    icon_id: str | None = None
    category: str = "general"
    parameters: list[ToolFieldSchema] = Field(default_factory=list)
    credential_definition_keys: list[str] = Field(default_factory=list)
    requires_credential: bool = False


# ---- CRUD ------------------------------------------------------------------


class ToolCreate(BaseModel):
    definition_key: str
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None
    enabled: bool = True


class ToolPatch(BaseModel):
    """Greenfield PATCH payload for ``PATCH /api/tools/{id}``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    parameters: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None
    enabled: bool | None = None


class ToolInstanceResponse(BaseModel):
    """Greenfield response for ``GET /api/tools/...``."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    definition_key: str
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None
    enabled: bool = True
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---- Run -------------------------------------------------------------------


class ToolRunRequest(BaseModel):
    runtime_args: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    success: bool
    result: Any = None
    error: str | None = None
    http_status: int | None = None
    duration_ms: int = 0


# ---- Legacy shims (deleted in M5) -----------------------------------------


class ToolType(enum.StrEnum):
    """Legacy 4-way classifier. M5 deletes this once chat_service is rewired."""

    BUILTIN = "builtin"
    PREBUILT = "prebuilt"
    CUSTOM = "custom"
    MCP = "mcp"


class ToolCustomCreate(BaseModel):
    """Legacy CUSTOM-tool create payload — kept for backward import compat."""

    name: str
    description: str | None = None
    api_url: str
    http_method: str = "GET"
    parameters_schema: dict[str, Any] | None = None
    auth_type: str | None = None
    connection_id: uuid.UUID


class LegacyToolResponse(BaseModel):
    """Legacy ToolResponse — exposed under the alias ``ToolResponseLegacy``."""

    id: uuid.UUID
    type: str
    provider_name: str | None = None
    is_system: bool = False
    connection_id: uuid.UUID | None = None
    name: str
    description: str | None = None
    parameters_schema: dict[str, Any] | None = None
    api_url: str | None = None
    http_method: str | None = None
    auth_type: str | None = None
    tags: list[str] | None = None
    agent_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class DiscoverToolItem(BaseModel):
    tool: LegacyToolResponse
    status: str


class DiscoverToolsResponse(BaseModel):
    connection_id: uuid.UUID
    server_info: dict[str, Any] = Field(default_factory=dict)
    items: list[DiscoverToolItem] = Field(default_factory=list)


class ToolUpdate(BaseModel):
    """Legacy PATCH payload — single ``connection_id`` field. M5 deletes."""

    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID | None = None


# Public alias preserved for backward compatibility — tests + legacy imports.
ToolResponse = LegacyToolResponse
