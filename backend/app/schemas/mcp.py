"""Pydantic schemas for the MCP server / MCP tool API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.mcp_server import STATUSES, TRANSPORTS

Transport = Literal["stdio", "sse", "streamable_http"]
Status = Literal[
    "unknown", "connected", "auth_needed", "unreachable", "disabled"
]

assert set(TRANSPORTS) == {"stdio", "sse", "streamable_http"}
assert set(STATUSES) == {
    "unknown",
    "connected",
    "auth_needed",
    "unreachable",
    "disabled",
}


# ---- McpServer -------------------------------------------------------------


class McpServerCreate(BaseModel):
    name: str
    description: str | None = None
    transport: Transport
    url: str | None = None
    command: str | None = None
    args: list[Any] = Field(default_factory=list)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None


class McpServerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    transport: Transport | None = None
    url: str | None = None
    command: str | None = None
    args: list[Any] | None = None
    env_vars: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None
    status: Status | None = None


class McpToolResponse(BaseModel):
    id: uuid.UUID
    server_id: uuid.UUID
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class McpServerResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str | None = None
    transport: str
    url: str | None = None
    command: str | None = None
    args: list[Any] = Field(default_factory=list)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None
    status: str
    last_pinged_at: datetime | None = None
    last_tool_count: int | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class McpServerDetailResponse(McpServerResponse):
    tools: list[McpToolResponse] = Field(default_factory=list)


class McpTestResponse(BaseModel):
    success: bool
    status: str
    server_info: dict[str, Any] = Field(default_factory=dict)
    tool_count: int = 0
    error: str | None = None


class McpDiscoverResponse(BaseModel):
    success: bool
    status: str
    tools: list[McpToolResponse] = Field(default_factory=list)
    error: str | None = None


# ---- Probe (preview without persistence) -----------------------------------


class McpProbeRequest(BaseModel):
    """Body for ``POST /api/mcp-servers/probe``.

    Connects to an MCP server and lists its tools without persisting anything.
    Used by the wizard's Step 3 to preview tools before the user commits with
    [Add]. Either provide manual fields or a ``registry_key`` to hydrate from
    the curated catalog.
    """

    transport: Transport | None = None
    url: str | None = None
    command: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None
    registry_key: str | None = None


class McpProbeTool(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpProbeResponse(BaseModel):
    success: bool
    server_info: dict[str, Any] = Field(default_factory=dict)
    tools: list[McpProbeTool] = Field(default_factory=list)
    error: str | None = None


class McpToolWithServerResponse(BaseModel):
    """Flat row used by the unified agent Tools picker — embeds the parent
    server's id/name so the UI can group / label without an extra fetch."""

    id: uuid.UUID
    name: str
    description: str | None = None
    enabled: bool
    server_id: uuid.UUID
    server_name: str


# ---- Registry (curated catalog) --------------------------------------------


class McpRegistryEntry(BaseModel):
    """A single entry from ``app/data/mcp_server_registry.json``."""

    key: str
    display_name: str
    description: str | None = None
    icon_id: str | None = None
    transport: Transport
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    credential_definition_key: str | None = None
    documentation_url: str | None = None


class McpServerCreateFromRegistry(BaseModel):
    """Body for ``POST /api/mcp-servers/from-registry``.

    Hydrates a :class:`McpServer` row from a registry entry. ``name`` lets
    the user override the display name (defaults to the registry's
    ``display_name`` when omitted on the client side); ``credential_id`` is
    optional — users can register the server first and bind a credential
    later via PATCH.
    """

    registry_key: str
    name: str
    credential_id: uuid.UUID | None = None
