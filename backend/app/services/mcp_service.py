"""MCP server / tool domain service (BE-S2).

Owns queries, mutations and side effects (audit records, runtime cache
invalidation) for the MCP domain. Routers keep HTTP concerns only: schema
conversion, ``Depends`` guards, and transaction commits.

Transaction policy: the service ``flush``es, the calling router ``commit``s.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.validation import require_user_credential
from app.dependencies import CurrentUser
from app.error_codes import mcp_server_not_found
from app.mcp import discovery as mcp_discovery
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.schemas.mcp import (
    McpImportError,
    McpImportRequest,
    McpImportResult,
    McpServerCreate,
    McpServerUpdate,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

# -- Queries -------------------------------------------------------------------


async def load_owned(db: AsyncSession, server_id: uuid.UUID, user_id: uuid.UUID) -> McpServer:
    row = (
        await db.execute(
            select(McpServer).where(McpServer.id == server_id, McpServer.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise mcp_server_not_found()
    return row


async def load_tools_for(db: AsyncSession, server_id: uuid.UUID) -> list[McpTool]:
    rows = (
        (
            await db.execute(
                select(McpTool).where(McpTool.server_id == server_id).order_by(McpTool.name)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def list_servers(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    order_by_name: bool = False,
) -> list[McpServer]:
    order = McpServer.name if order_by_name else McpServer.created_at.desc()
    rows = (
        (await db.execute(select(McpServer).where(McpServer.user_id == user_id).order_by(order)))
        .scalars()
        .all()
    )
    return list(rows)


async def list_all_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[tuple[McpTool, str]]:
    """Every MCP tool across the user's servers, paired with the server name."""

    rows = (
        await db.execute(
            select(McpTool, McpServer.name)
            .join(McpServer, McpTool.server_id == McpServer.id)
            .where(McpServer.user_id == user_id)
            .order_by(McpServer.name, McpTool.name)
        )
    ).all()
    return [(tool, server_name) for tool, server_name in rows]


# -- Validation ------------------------------------------------------------------


def validate_payload_consistency(
    transport: str | None,
    url: str | None,
    command: str | None,
) -> None:
    if transport in {"sse", "streamable_http"} and not url:
        raise HTTPException(
            status_code=422,
            detail=f"transport '{transport}' requires url",
        )
    if transport == "stdio" and not command:
        raise HTTPException(
            status_code=422,
            detail="stdio transport requires command",
        )


# -- Mutations -------------------------------------------------------------------


async def create_server(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    data: McpServerCreate,
) -> McpServer:
    validate_payload_consistency(data.transport, data.url, data.command)
    if data.credential_id is not None:
        await require_user_credential(db, credential_id=data.credential_id, user_id=user_id)
    server = McpServer(
        user_id=user_id,
        name=data.name,
        description=data.description,
        transport=data.transport,
        url=data.url,
        command=data.command,
        args=list(data.args or []),
        env_vars=dict(data.env_vars or {}),
        headers=dict(data.headers or {}),
        credential_id=data.credential_id,
        status="unknown",
    )
    db.add(server)
    await db.flush()
    return server


async def create_server_from_registry_entry(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    credential_id: uuid.UUID | None,
    entry: dict[str, Any],
) -> McpServer:
    """Create a server from a resolved catalog entry (registry lookup is the
    router's concern — unknown keys are an HTTP 400 there)."""

    transport = entry.get("transport")
    url = entry.get("url")
    command = entry.get("command")
    validate_payload_consistency(transport, url, command)
    if credential_id is not None:
        await require_user_credential(db, credential_id=credential_id, user_id=user_id)

    server = McpServer(
        user_id=user_id,
        name=name,
        description=entry.get("description"),
        transport=transport,
        url=url,
        command=command,
        args=list(entry.get("args") or []),
        env_vars=dict(entry.get("env_vars") or {}),
        headers={},
        credential_id=credential_id,
        status="unknown",
    )
    db.add(server)
    await db.flush()
    return server


async def update_server(
    db: AsyncSession,
    *,
    server: McpServer,
    user_id: uuid.UUID,
    data: McpServerUpdate,
) -> McpServer:
    fields_set = data.model_fields_set

    if "name" in fields_set and data.name is not None:
        server.name = data.name
    if "description" in fields_set:
        server.description = data.description
    if "transport" in fields_set and data.transport is not None:
        server.transport = data.transport
    if "url" in fields_set:
        server.url = data.url
    if "command" in fields_set:
        server.command = data.command
    if "args" in fields_set and data.args is not None:
        server.args = list(data.args)
    if "env_vars" in fields_set and data.env_vars is not None:
        server.env_vars = dict(data.env_vars)
    if "headers" in fields_set and data.headers is not None:
        server.headers = dict(data.headers)
    if "credential_id" in fields_set:
        if data.credential_id is not None:
            await require_user_credential(db, credential_id=data.credential_id, user_id=user_id)
        server.credential_id = data.credential_id
    if "status" in fields_set and data.status is not None:
        server.status = data.status

    validate_payload_consistency(server.transport, server.url, server.command)
    await db.flush()
    return server


async def delete_server(db: AsyncSession, *, server: McpServer) -> None:
    # Manual cascade for SQLite tests where ondelete=CASCADE isn't enforced.
    for row in await load_tools_for(db, server.id):
        await db.delete(row)
    await db.delete(server)
    await db.flush()


async def import_servers(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    data: McpImportRequest,
) -> McpImportResult:
    """Bulk import MCP server definitions (see router docstring for shape)."""

    result = McpImportResult()

    # Pre-load existing servers by name so we don't re-query inside the loop.
    existing_rows = (
        (await db.execute(select(McpServer).where(McpServer.user_id == user_id))).scalars().all()
    )
    by_name: dict[str, McpServer] = {row.name: row for row in existing_rows}

    # Validate referenced credentials up-front (a 400 is friendlier than
    # silently committing rows with a dangling FK).
    referenced_creds: set[uuid.UUID] = {
        e.credential_id for e in data.mcpServers.values() if e.credential_id is not None
    }
    valid_creds: set[uuid.UUID] = set()
    if referenced_creds:
        owned = (
            (
                await db.execute(
                    select(Credential.id).where(
                        Credential.user_id == user_id,
                        Credential.id.in_(referenced_creds),
                    )
                )
            )
            .scalars()
            .all()
        )
        valid_creds = set(owned)

    for name, entry in data.mcpServers.items():
        try:
            transport = entry.transport
            if transport is None and entry.command:
                transport = "stdio"
            if transport is None:
                result.errors.append(
                    McpImportError(
                        name=name,
                        reason="transport could not be inferred (provide transport or command)",
                    )
                )
                continue

            # Mirror create-server validation so import doesn't smuggle in
            # rows that wouldn't survive POST /api/mcp-servers.
            if transport in {"sse", "streamable_http"} and not entry.url:
                result.errors.append(
                    McpImportError(
                        name=name,
                        reason=f"transport '{transport}' requires url",
                    )
                )
                continue
            if transport == "stdio" and not entry.command:
                result.errors.append(
                    McpImportError(
                        name=name,
                        reason="stdio transport requires command",
                    )
                )
                continue

            credential_id = entry.credential_id
            if credential_id is not None and credential_id not in valid_creds:
                result.errors.append(
                    McpImportError(
                        name=name,
                        reason=f"credential_id {credential_id} not found",
                    )
                )
                continue

            existing = by_name.get(name)
            if existing is not None and not data.overwrite:
                result.skipped += 1
                continue

            if existing is None:
                server = McpServer(
                    user_id=user_id,
                    name=name,
                    description=entry.description,
                    transport=transport,
                    url=entry.url,
                    command=entry.command,
                    args=list(entry.args or []),
                    env_vars=dict(entry.env or {}),
                    headers=dict(entry.headers or {}),
                    credential_id=credential_id,
                    status="unknown",
                )
                db.add(server)
                by_name[name] = server
                result.created += 1
            else:
                existing.description = entry.description
                existing.transport = transport
                existing.url = entry.url
                existing.command = entry.command
                existing.args = list(entry.args or [])
                existing.env_vars = dict(entry.env or {})
                existing.headers = dict(entry.headers or {})
                existing.credential_id = credential_id
                result.updated += 1
        except Exception as exc:  # noqa: BLE001 — record + continue
            result.errors.append(McpImportError(name=name, reason=str(exc)))

    await db.flush()
    return result


# -- Side effects ----------------------------------------------------------------


async def invalidate_runtime_mcp_cache() -> None:
    # Deferred import — app.agent_runtime pulls in the full runtime stack and
    # importing it at module load would create a services <-> agent_runtime
    # cycle (BE-S4 tracks the root cause).
    from app.agent_runtime.mcp_cache import clear_mcp_tool_cache

    await clear_mcp_tool_cache()


def key_names(value: dict[str, Any] | None) -> list[str]:
    return sorted(str(key) for key in (value or {}))


def server_metadata(server: McpServer) -> dict[str, Any]:
    return {
        "transport": server.transport,
        "has_url": bool(server.url),
        "command_present": bool(server.command),
        "arg_count": len(server.args or []),
        "env_var_keys": key_names(server.env_vars),
        "header_keys": key_names(server.headers),
        "credential_bound": server.credential_id is not None,
        "status": server.status,
    }


async def record_server_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    server: McpServer,
    outcome: str = "success",
    reason_code: str | None = None,
    reason_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_self_event(
        db,
        user,
        action=action,
        target_type="mcp_server",
        target_id=server.id,
        target_name=server.name,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        request=request,
        metadata={**server_metadata(server), **(metadata or {})},
    )


async def record_probe_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    registry_key: str | None,
    transport: str | None,
    url: str | None,
    command_present: bool,
    headers: dict[str, Any],
    credential_bound: bool,
    result: dict[str, Any],
) -> None:
    await audit_service.record_self_event(
        db,
        user,
        action="mcp_server.probe",
        target_type="mcp_server_probe",
        target_name=registry_key,
        outcome="success" if result.get("success") else "failure",
        reason_code=None if result.get("success") else "mcp_probe_failed",
        reason_message=result.get("error"),
        request=request,
        metadata={
            "registry_key": registry_key,
            "transport": transport,
            "has_url": bool(url),
            "command_present": command_present,
            "header_keys": key_names(headers),
            "credential_bound": credential_bound,
            "tool_count": len(result.get("tools") or []),
        },
    )


async def record_import_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    result: McpImportResult,
    entry_count: int,
    overwrite: bool,
) -> None:
    import_outcome = (
        "failure" if result.errors and result.created == 0 and result.updated == 0 else "success"
    )
    await audit_service.record_self_event(
        db,
        user,
        action="mcp_server.import",
        target_type="mcp_server",
        outcome=import_outcome,
        reason_code="mcp_import_errors" if result.errors else None,
        request=request,
        metadata={
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "error_count": len(result.errors),
            "entry_count": entry_count,
            "overwrite": overwrite,
        },
    )


# -- Health polling (BE-S9 — 본문은 app.scheduler에서 이관) ----------------------


async def poll_mcp_servers_health(
    *,
    session_factory: Callable[[], AsyncSession],
) -> dict[str, int]:
    """Run a quick connectivity probe against every enabled MCP server.

    Distinct from ``health_check_all_active`` (which writes a persistent
    history row): this job only refreshes the lightweight
    ``health_status`` / ``health_polled_at`` / ``health_message`` columns
    so the list view can show a fresh dot without paying for a full sweep.
    """

    counters = {"checked": 0, "ok": 0, "error": 0}
    polled_at = datetime.now(UTC).replace(tzinfo=None)

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(McpServer).where(
                        or_(
                            McpServer.is_system.is_(True),
                            McpServer.status != "disabled",
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        for server in rows:
            counters["checked"] += 1
            try:
                probe = await mcp_discovery.test_server(db, server)
            except Exception as exc:  # noqa: BLE001 — keep the sweep alive
                logger.exception("mcp health poll failed for server %s", server.id)
                server.health_status = "error"
                server.health_polled_at = polled_at
                server.health_message = str(exc)
                counters["error"] += 1
                continue

            server.health_polled_at = polled_at
            if probe.get("success"):
                server.health_status = "ok"
                server.health_message = None
                counters["ok"] += 1
            else:
                server.health_status = "error"
                server.health_message = probe.get("error")
                counters["error"] += 1

        await db.commit()

    logger.info("mcp health poll finished: %s", counters)
    return counters
