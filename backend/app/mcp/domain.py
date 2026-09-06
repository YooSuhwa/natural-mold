"""MCP domain dataclasses (decoupled from ORM)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolDescriptor:
    """Normalized representation of a tool advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpServerInfo:
    """Server identity returned during ``initialize``."""

    name: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class McpAppRuntimeContext:
    """Server-owned identities attached to one discovered runtime tool."""

    conversation_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    credential_subject_user_id: uuid.UUID
    mcp_server_id: uuid.UUID
    mcp_tool_id: uuid.UUID
    remote_tool_name: str
    resource_uri: str
    tool_meta: dict[str, Any]


__all__ = ["McpAppRuntimeContext", "McpServerInfo", "McpToolDescriptor"]
