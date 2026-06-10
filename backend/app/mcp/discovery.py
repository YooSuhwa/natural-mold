"""MCP tool discovery — connect, list, upsert into ``mcp_tools``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import connect_and_list
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool


async def _probe(db: AsyncSession, server: McpServer) -> dict[str, Any]:
    auth = await resolve_mcp_auth(
        db,
        credential_id=server.credential_id,
        user_id=server.user_id,
        static_headers=server.headers,
    )
    if auth.error:
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": auth.error,
            "status": auth.status,
        }
    return await connect_and_list(
        transport=server.transport,
        url=server.url,
        headers=auth.headers,
        credentials=auth.credentials,
        command=server.command,
        args=server.args,
        env_vars=server.env_vars,
    )


async def test_server(db: AsyncSession, server: McpServer) -> dict[str, Any]:
    """Connectivity probe — updates ``status`` / ``last_pinged_at`` only."""

    probe = await _probe(db, server)
    server.last_pinged_at = datetime.now(UTC).replace(tzinfo=None)
    if probe["success"]:
        server.status = "connected"
        server.last_tool_count = len(probe["tools"])
        server.last_error = None
    else:
        # If the error mentions auth/401/403, mark auth_needed; otherwise
        # mark unreachable.
        err = (probe.get("error") or "").lower()
        if probe.get("status") == "auth_needed" or any(
            token in err for token in ("401", "403", "unauthorized", "forbidden")
        ):
            server.status = "auth_needed"
        else:
            server.status = "unreachable"
        server.last_error = probe.get("error")
    return probe


async def discover_tools(
    db: AsyncSession,
    server: McpServer,
) -> tuple[dict[str, Any], list[McpTool]]:
    """Probe the server and upsert the returned tools into ``mcp_tools``.

    Returns ``(probe_dict, persisted_tools)``. The probe dict is the same shape
    as :func:`app.mcp.client.connect_and_list` so callers can surface error
    messages verbatim. Persisted tools are returned post-flush so their IDs
    are populated.
    """

    probe = await test_server(db, server)
    if not probe["success"]:
        return probe, []

    existing = (
        (await db.execute(select(McpTool).where(McpTool.server_id == server.id))).scalars().all()
    )
    by_name = {t.name: t for t in existing}

    now = datetime.now(UTC).replace(tzinfo=None)
    seen_names: set[str] = set()
    persisted: list[McpTool] = []
    for descriptor in probe["tools"]:
        name = descriptor["name"]
        seen_names.add(name)
        row = by_name.get(name)
        if row is None:
            row = McpTool(
                server_id=server.id,
                name=name,
                description=descriptor.get("description") or None,
                input_schema=descriptor.get("input_schema") or {},
                enabled=True,
                last_seen_at=now,
            )
            db.add(row)
        else:
            row.description = descriptor.get("description") or row.description
            row.input_schema = descriptor.get("input_schema") or {}
            row.last_seen_at = now
        persisted.append(row)

    # Tools no longer reported by the server are NOT deleted — they may still
    # be linked to agents (``agent_mcp_tools``). Their stale state can be
    # detected via ``last_seen_at`` lagging behind the server's last poll.

    await db.flush()
    return probe, persisted


__all__ = ["discover_tools", "test_server"]
