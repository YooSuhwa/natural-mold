"""MCP domain dataclasses (decoupled from ORM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolDescriptor:
    """Normalized representation of a tool advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpServerInfo:
    """Server identity returned during ``initialize``."""

    name: str | None = None
    version: str | None = None


__all__ = ["McpServerInfo", "McpToolDescriptor"]
