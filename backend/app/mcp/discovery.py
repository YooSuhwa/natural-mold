"""MCP tool discovery — connect, list, upsert into ``mcp_tools``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.mcp.client import connect_and_list
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool


async def _decrypt_credential(
    db: AsyncSession, credential_id: Any
) -> dict[str, Any] | None:
    if credential_id is None:
        return None
    row = (
        await db.execute(select(Credential).where(Credential.id == credential_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    return await credential_service.decrypt_with_external(row.data_encrypted)


async def _probe(
    db: AsyncSession, server: McpServer
) -> dict[str, Any]:
    credentials = await _decrypt_credential(db, server.credential_id)
    return await connect_and_list(
        transport=server.transport,
        url=server.url,
        headers=server.headers,
        credentials=credentials,
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
        if any(token in err for token in ("401", "403", "unauthorized", "forbidden")):
            server.status = "auth_needed"
        else:
            server.status = "unreachable"
        server.last_error = probe.get("error")
    return probe


async def discover_tools(
    db: AsyncSession, server: McpServer
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
        await db.execute(
            select(McpTool).where(McpTool.server_id == server.id)
        )
    ).scalars().all()
    by_name = {t.name: t for t in existing}

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
            )
            db.add(row)
        else:
            row.description = descriptor.get("description") or row.description
            row.input_schema = descriptor.get("input_schema") or {}
        persisted.append(row)

    # Drop tools that are no longer reported by the server. The unique
    # constraint on (server_id, name) ensures we don't double up.
    for name, row in by_name.items():
        if name not in seen_names:
            await db.delete(row)

    await db.flush()
    return probe, persisted


__all__ = ["discover_tools", "test_server"]
