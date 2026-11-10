"""MCP tool discovery — connect, list, upsert into ``mcp_tools`` (reviewed: app.mcp.review)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import registrations
from app.authz.mcp_probe_scope import require_mcp_probe_scope
from app.authz.notifications import Target, notify, resource_link, resource_managers
from app.mcp import review
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import connect_and_list
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool

REVIEW_NEEDED_KIND = "mcp_tool.review_needed"


async def _probe(db: AsyncSession, server: McpServer) -> dict[str, Any]:
    credential = await require_mcp_probe_scope(db, server, server.credential_id)
    auth = await resolve_mcp_auth(
        db,
        credential_id=server.credential_id,
        user_id=credential.user_id if credential is not None else server.user_id,
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


async def _server_tools(db: AsyncSession, server: McpServer) -> list[McpTool]:
    rows = await db.execute(select(McpTool).where(McpTool.server_id == server.id))
    return list(rows.scalars().all())


async def notify_review_needed(db: AsyncSession, server: McpServer, tools: list[McpTool]) -> None:
    """Tell the server's managers that tools wait for their review (company servers)."""
    if not tools or server.org_id is None or server.tenant_id is None:
        return
    # A server registered under review: its tool versions go to the administrators (G10).
    if await registrations.tools_changed(db, server):
        return
    managers = await resource_managers(db, server.org_id, "mcp_server", server.id, mode="enforce")
    await notify(
        db,
        kind=REVIEW_NEEDED_KIND,
        recipients=sorted(managers),
        tenant_id=server.tenant_id,
        org_id=server.org_id,
        target=Target("mcp_server", server.id, server.name),
        payload={"tool_count": len(tools)},
        link=resource_link("mcp_server", server.id),
    )


async def test_server(db: AsyncSession, server: McpServer) -> dict[str, Any]:
    """Connectivity probe: ``status``/``last_pinged_at``, and review flags on known tools.

    A changed definition of an existing tool waits for review (``app.mcp.review``); new tools
    are added only by ``discover_tools``.
    """

    probe = await _probe(db, server)
    server.last_pinged_at = datetime.now(UTC).replace(tzinfo=None)
    if probe["success"]:
        server.status = "connected"
        server.last_tool_count = len(probe["tools"])
        server.last_error = None
        observed = review.observe(await _server_tools(db, server), probe["tools"], server.url)
        await db.flush()
        await notify_review_needed(db, server, list(observed.flagged))
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

    by_name = {t.name: t for t in await _server_tools(db, server)}
    added: list[McpTool] = []
    persisted: list[McpTool] = []
    for descriptor in probe["tools"]:
        name = descriptor["name"]
        row = by_name.get(name)
        if row is None:
            # D11: a newly discovered tool is hidden until a server manager approves it.
            row = review.new_pending_tool(
                server.id, name, review.Definition.from_descriptor(descriptor), server.url
            )
            db.add(row)
            added.append(row)
        persisted.append(row)
    await db.flush()
    await notify_review_needed(db, server, added)

    # Tools no longer reported by the server are NOT deleted — they may still
    # be linked to agents (``agent_mcp_tools``). Their stale state can be
    # detected via ``last_seen_at`` lagging behind the server's last poll.

    await db.flush()
    return probe, persisted


__all__ = ["discover_tools", "test_server"]
