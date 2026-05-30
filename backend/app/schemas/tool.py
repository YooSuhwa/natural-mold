"""Pydantic schemas for the Tool API (greenfield)."""

from __future__ import annotations

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
    runtime_only: bool = False


class ToolDefinitionSchema(BaseModel):
    key: str
    display_name: str
    description: str
    icon_id: str | None = None
    category: str = "general"
    parameters: list[ToolFieldSchema] = Field(default_factory=list)
    credential_definition_keys: list[str] = Field(default_factory=list)
    requires_credential: bool = False
    risk: dict[str, Any] = Field(default_factory=dict)


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
