"""Backwards-compatible shim for the assistant/builder catalog.

The greenfield Tool/Credential domain owns CRUD via :mod:`app.routers.tools`
and :mod:`app.tools.runner`. The Builder/Assistant sub-agents only need a
read-only catalog (name + description) so they can suggest tools to the user;
this thin module supplies that view without re-importing any of the legacy
PREBUILT/CUSTOM scaffolding deleted in M5.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.tool import Tool


async def get_tools_catalog(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return the user's enabled tools + system tools + MCP tools (per-user servers)."""

    tool_rows = await db.execute(
        select(Tool.id, Tool.name, Tool.description, Tool.definition_key).where(
            or_(Tool.user_id == user_id, Tool.user_id.is_(None))
        )
    )
    items: list[dict[str, Any]] = [
        {
            "id": str(row.id),
            "kind": "tool",
            "name": row.name,
            "description": row.description or "",
            "definition_key": row.definition_key,
        }
        for row in tool_rows.all()
    ]

    mcp_rows = await db.execute(
        select(
            McpTool.id,
            McpTool.name,
            McpTool.description,
            McpTool.enabled,
            McpServer.id.label("server_id"),
            McpServer.name.label("server_name"),
        )
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(McpServer.user_id == user_id)
    )
    items.extend(
        {
            "id": str(row.id),
            "kind": "mcp",
            "name": row.name,
            "description": row.description or "",
            "server_id": str(row.server_id),
            "server_name": row.server_name,
            "enabled": bool(row.enabled),
        }
        for row in mcp_rows.all()
    )

    return items


__all__ = ["get_tools_catalog"]
