"""MCP server / tool API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.validation import require_user_credential
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.mcp import discovery
from app.mcp.client import connect_and_list
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.schemas.mcp import (
    McpDiscoverResponse,
    McpExportEntry,
    McpExportResponse,
    McpImportError,
    McpImportRequest,
    McpImportResult,
    McpProbeRequest,
    McpProbeResponse,
    McpProbeTool,
    McpRegistryEntry,
    McpServerCreate,
    McpServerCreateFromRegistry,
    McpServerDetailResponse,
    McpServerResponse,
    McpServerUpdate,
    McpTestResponse,
    McpToolResponse,
    McpToolWithServerResponse,
)
from app.services import audit_service
from app.services import mcp_registry as mcp_registry_service

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp"])
# Catalog of well-known MCP servers — separate prefix so router mount paths
# stay parallel to the credentials catalog (``/api/credential-types``).
catalog_router = APIRouter(prefix="/api/mcp-server-types", tags=["mcp"])


# -- Helpers -----------------------------------------------------------------


def _server_to_response(server: McpServer) -> McpServerResponse:
    return McpServerResponse(
        id=server.id,
        user_id=server.user_id,
        name=server.name,
        description=server.description,
        transport=server.transport,
        url=server.url,
        command=server.command,
        args=server.args or [],
        env_vars=server.env_vars or {},
        headers=server.headers or {},
        credential_id=server.credential_id,
        status=server.status,
        last_pinged_at=server.last_pinged_at,
        last_tool_count=server.last_tool_count,
        last_error=server.last_error,
        is_system=bool(getattr(server, "is_system", False)),
        health_status=getattr(server, "health_status", None),
        health_polled_at=getattr(server, "health_polled_at", None),
        health_message=getattr(server, "health_message", None),
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _tool_to_response(tool: McpTool) -> McpToolResponse:
    return McpToolResponse(
        id=tool.id,
        server_id=tool.server_id,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema or {},
        enabled=tool.enabled,
        last_seen_at=getattr(tool, "last_seen_at", None),
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


async def _load_owned(
    db: AsyncSession, server_id: uuid.UUID, user_id: uuid.UUID
) -> McpServer:
    row = (
        await db.execute(
            select(McpServer).where(
                McpServer.id == server_id, McpServer.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    return row


async def _load_tools_for(db: AsyncSession, server_id: uuid.UUID) -> list[McpTool]:
    rows = (
        await db.execute(
            select(McpTool)
            .where(McpTool.server_id == server_id)
            .order_by(McpTool.name)
        )
    ).scalars().all()
    return list(rows)


def _validate_payload_consistency(
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


async def _invalidate_runtime_mcp_cache() -> None:
    from app.agent_runtime.mcp_cache import clear_mcp_tool_cache

    await clear_mcp_tool_cache()


def _keys(value: dict[str, Any] | None) -> list[str]:
    return sorted(str(key) for key in (value or {}))


def _mcp_server_metadata(server: McpServer) -> dict[str, Any]:
    return {
        "transport": server.transport,
        "has_url": bool(server.url),
        "command_present": bool(server.command),
        "arg_count": len(server.args or []),
        "env_var_keys": _keys(server.env_vars),
        "header_keys": _keys(server.headers),
        "credential_bound": server.credential_id is not None,
        "status": server.status,
    }


async def _record_mcp_audit(
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
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="mcp_server",
        target_id=server.id,
        target_name_snapshot=server.name,
        target_owner_user_id=user.id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        request=request,
        metadata={**_mcp_server_metadata(server), **(metadata or {})},
    )


# -- CRUD --------------------------------------------------------------------


@router.get("", response_model=list[McpServerResponse])
async def list_servers(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[McpServerResponse]:
    rows = (
        await db.execute(
            select(McpServer)
            .where(McpServer.user_id == user.id)
            .order_by(McpServer.created_at.desc())
        )
    ).scalars().all()
    return [_server_to_response(r) for r in rows]


@router.post("", response_model=McpServerResponse, status_code=201)
async def create_server(
    payload: McpServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpServerResponse:
    _validate_payload_consistency(payload.transport, payload.url, payload.command)
    if payload.credential_id is not None:
        await require_user_credential(
            db, credential_id=payload.credential_id, user_id=user.id
        )
    server = McpServer(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        transport=payload.transport,
        url=payload.url,
        command=payload.command,
        args=list(payload.args or []),
        env_vars=dict(payload.env_vars or {}),
        headers=dict(payload.headers or {}),
        credential_id=payload.credential_id,
        status="unknown",
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.create",
        server=server,
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.post(
    "/from-registry", response_model=McpServerResponse, status_code=201
)
async def create_server_from_registry(
    payload: McpServerCreateFromRegistry,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpServerResponse:
    """Create a server from a curated catalog entry.

    Pre-fills transport / URL / stdio command / env_var template from the
    registry, then sticks the user-supplied name and (optional)
    ``credential_id`` on top. Unknown ``registry_key`` is a 400 — the
    catalog is the source of truth.
    """

    entry = mcp_registry_service.get_registry_entry(payload.registry_key)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown registry entry '{payload.registry_key}'",
        )

    transport = entry.get("transport")
    url = entry.get("url")
    command = entry.get("command")
    _validate_payload_consistency(transport, url, command)
    if payload.credential_id is not None:
        await require_user_credential(
            db, credential_id=payload.credential_id, user_id=user.id
        )

    server = McpServer(
        user_id=user.id,
        name=payload.name,
        description=entry.get("description"),
        transport=transport,
        url=url,
        command=command,
        args=list(entry.get("args") or []),
        env_vars=dict(entry.get("env_vars") or {}),
        headers={},
        credential_id=payload.credential_id,
        status="unknown",
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.create",
        server=server,
        metadata={"registry_key": payload.registry_key},
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.post("/probe", response_model=McpProbeResponse)
async def probe_mcp_server(
    payload: McpProbeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpProbeResponse:
    """Connect to an MCP server without persisting it.

    Powers the wizard's "preview before commit" flow — returns the same
    tool descriptors the regular discover path would import, so the UI can
    render results identically. No DB writes happen here, so a user who
    abandons the wizard leaves no orphan rows.
    """

    transport = payload.transport
    url = payload.url
    headers: dict[str, Any] = dict(payload.headers or {})

    if payload.registry_key:
        entry = mcp_registry_service.get_registry_entry(payload.registry_key)
        if entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown registry entry '{payload.registry_key}'",
            )
        transport = transport or entry.get("transport")
        url = url or entry.get("url")
        registry_headers = entry.get("headers") or {}
        headers = {**headers, **registry_headers}

    if transport is None:
        raise HTTPException(
            status_code=422,
            detail="transport is required (or provide registry_key)",
        )
    _validate_payload_consistency(transport, url, payload.command)

    credentials: dict[str, Any] | None = None
    if payload.credential_id is not None:
        credential = (
            await db.execute(
                select(Credential).where(
                    Credential.id == payload.credential_id,
                    Credential.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if credential is None:
            raise HTTPException(status_code=404, detail="credential not found")
        credentials = await credential_service.decrypt_with_external(
            credential.data_encrypted
        )

    result = await connect_and_list(
        transport=transport,
        url=url,
        headers=headers,
        credentials=credentials,
        user_id=user.id,
        credential_id=payload.credential_id,
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="mcp_server.probe",
        target_type="mcp_server_probe",
        target_name_snapshot=payload.registry_key,
        target_owner_user_id=user.id,
        outcome="success" if result.get("success") else "failure",
        reason_code=None if result.get("success") else "mcp_probe_failed",
        reason_message=result.get("error"),
        request=request,
        metadata={
            "registry_key": payload.registry_key,
            "transport": transport,
            "has_url": bool(url),
            "command_present": bool(payload.command),
            "header_keys": _keys(headers),
            "credential_bound": payload.credential_id is not None,
            "tool_count": len(result.get("tools") or []),
        },
    )
    await db.commit()

    return McpProbeResponse(
        success=bool(result.get("success")),
        server_info=result.get("server_info") or {},
        tools=[
            McpProbeTool(
                name=t["name"],
                description=t.get("description"),
                input_schema=t.get("input_schema") or {},
            )
            for t in (result.get("tools") or [])
        ],
        error=result.get("error"),
    )


@router.get("/all-tools", response_model=list[McpToolWithServerResponse])
async def list_all_user_mcp_tools(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[McpToolWithServerResponse]:
    """All MCP tools across the user's servers — powers the unified Tools
    picker (Tools tab → MCP source) so the user can bind individual tools
    to an agent without first navigating to each server detail page."""

    rows = (
        await db.execute(
            select(McpTool, McpServer.name)
            .join(McpServer, McpTool.server_id == McpServer.id)
            .where(McpServer.user_id == user.id)
            .order_by(McpServer.name, McpTool.name)
        )
    ).all()
    return [
        McpToolWithServerResponse(
            id=tool.id,
            name=tool.name,
            description=tool.description,
            enabled=tool.enabled,
            server_id=tool.server_id,
            server_name=server_name,
        )
        for tool, server_name in rows
    ]


# -- Import / Export ---------------------------------------------------------
# NOTE: must be declared before ``/{server_id}`` so the literal path segments
# don't get matched as a UUID and bounced with 422.


@router.post("/import", response_model=McpImportResult)
async def import_servers(
    payload: McpImportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpImportResult:
    """Bulk import MCP server definitions.

    Accepts the Claude Desktop ``{mcpServers: {<name>: {...}}}`` shape. Each
    entry can use ``command`` / ``args`` / ``env`` (stdio, inferred when
    ``transport`` is omitted) or ``transport`` + ``url`` + ``headers``
    (Moldy network extension). ``credential_id`` is honoured when present
    and owned by the caller.

    With ``overwrite=false`` (default) duplicates are skipped; with
    ``overwrite=true`` an existing same-named server is updated in place
    (its tool links are preserved because the row keeps its id).
    """

    result = McpImportResult()

    # Pre-load existing servers by name so we don't re-query inside the loop.
    existing_rows = (
        await db.execute(
            select(McpServer).where(McpServer.user_id == user.id)
        )
    ).scalars().all()
    by_name: dict[str, McpServer] = {row.name: row for row in existing_rows}

    # Validate referenced credentials up-front (a 400 is friendlier than
    # silently committing rows with a dangling FK).
    referenced_creds: set[uuid.UUID] = {
        e.credential_id
        for e in payload.mcpServers.values()
        if e.credential_id is not None
    }
    valid_creds: set[uuid.UUID] = set()
    if referenced_creds:
        owned = (
            await db.execute(
                select(Credential.id).where(
                    Credential.user_id == user.id,
                    Credential.id.in_(referenced_creds),
                )
            )
        ).scalars().all()
        valid_creds = set(owned)

    for name, entry in payload.mcpServers.items():
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
            if existing is not None and not payload.overwrite:
                result.skipped += 1
                continue

            if existing is None:
                server = McpServer(
                    user_id=user.id,
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

    await db.commit()
    import_outcome = (
        "failure"
        if result.errors and result.created == 0 and result.updated == 0
        else "success"
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="mcp_server.import",
        target_type="mcp_server",
        target_owner_user_id=user.id,
        outcome=import_outcome,
        reason_code="mcp_import_errors" if result.errors else None,
        request=request,
        metadata={
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "error_count": len(result.errors),
            "entry_count": len(payload.mcpServers),
            "overwrite": payload.overwrite,
        },
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return result


@router.get("/export", response_model=McpExportResponse)
async def export_servers(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> McpExportResponse:
    """Dump every server owned by the caller in import-compatible shape.

    Secrets are never inlined — ``env`` / ``headers`` keep their pre-resolution
    template strings, and ``credential_id`` is exported as a bare reference.
    """

    rows = (
        await db.execute(
            select(McpServer)
            .where(McpServer.user_id == user.id)
            .order_by(McpServer.name)
        )
    ).scalars().all()

    out: dict[str, McpExportEntry] = {}
    for row in rows:
        out[row.name] = McpExportEntry(
            transport=row.transport,
            command=row.command,
            args=list(row.args or []),
            env=dict(row.env_vars or {}),
            url=row.url,
            headers=dict(row.headers or {}),
            credential_id=row.credential_id,
            description=row.description,
        )
    return McpExportResponse(mcpServers=out)


@router.get("/{server_id}", response_model=McpServerDetailResponse)
async def get_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> McpServerDetailResponse:
    server = await _load_owned(db, server_id, user.id)
    tools = await _load_tools_for(db, server_id)
    base = _server_to_response(server)
    return McpServerDetailResponse(
        **base.model_dump(),
        tools=[_tool_to_response(t) for t in tools],
    )


@router.patch("/{server_id}", response_model=McpServerResponse)
async def update_server(
    server_id: uuid.UUID,
    payload: McpServerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpServerResponse:
    server = await _load_owned(db, server_id, user.id)
    fields_set = payload.model_fields_set

    if "name" in fields_set and payload.name is not None:
        server.name = payload.name
    if "description" in fields_set:
        server.description = payload.description
    if "transport" in fields_set and payload.transport is not None:
        server.transport = payload.transport
    if "url" in fields_set:
        server.url = payload.url
    if "command" in fields_set:
        server.command = payload.command
    if "args" in fields_set and payload.args is not None:
        server.args = list(payload.args)
    if "env_vars" in fields_set and payload.env_vars is not None:
        server.env_vars = dict(payload.env_vars)
    if "headers" in fields_set and payload.headers is not None:
        server.headers = dict(payload.headers)
    if "credential_id" in fields_set:
        if payload.credential_id is not None:
            await require_user_credential(
                db, credential_id=payload.credential_id, user_id=user.id
            )
        server.credential_id = payload.credential_id
    if "status" in fields_set and payload.status is not None:
        server.status = payload.status

    _validate_payload_consistency(server.transport, server.url, server.command)
    await db.commit()
    await db.refresh(server)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.update",
        server=server,
        metadata={
            "changed_fields": sorted(fields_set - {"headers", "env_vars"}),
            "headers_changed": "headers" in fields_set,
            "env_vars_changed": "env_vars" in fields_set,
            "credential_changed": "credential_id" in fields_set,
        },
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    server = await _load_owned(db, server_id, user.id)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.delete",
        server=server,
    )
    # Manual cascade for SQLite tests where ondelete=CASCADE isn't enforced.
    for row in await _load_tools_for(db, server_id):
        await db.delete(row)
    await db.delete(server)
    await db.commit()
    await _invalidate_runtime_mcp_cache()


# -- Connectivity probes ------------------------------------------------------


@router.post("/{server_id}/test", response_model=McpTestResponse)
async def test_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpTestResponse:
    server = await _load_owned(db, server_id, user.id)
    probe: dict[str, Any] = await discovery.test_server(db, server)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.test",
        server=server,
        outcome="success" if probe["success"] else "failure",
        reason_code=None if probe["success"] else "mcp_test_failed",
        reason_message=probe.get("error"),
        metadata={"tool_count": len(probe.get("tools") or [])},
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return McpTestResponse(
        success=probe["success"],
        status=server.status,
        server_info=probe.get("server_info") or {},
        tool_count=len(probe.get("tools") or []),
        error=probe.get("error"),
    )


@router.post("/{server_id}/discover", response_model=McpDiscoverResponse)
async def discover_server_tools(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpDiscoverResponse:
    server = await _load_owned(db, server_id, user.id)
    probe, tools = await discovery.discover_tools(db, server)
    await _record_mcp_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.discover",
        server=server,
        outcome="success" if probe["success"] else "failure",
        reason_code=None if probe["success"] else "mcp_discover_failed",
        reason_message=probe.get("error"),
        metadata={"tool_count": len(tools)},
    )
    await db.commit()
    await _invalidate_runtime_mcp_cache()
    return McpDiscoverResponse(
        success=probe["success"],
        status=server.status,
        tools=[_tool_to_response(t) for t in tools],
        error=probe.get("error"),
    )


# -- Registry catalog --------------------------------------------------------


@catalog_router.get("", response_model=list[McpRegistryEntry])
async def list_registry_entries() -> list[McpRegistryEntry]:
    """Return every entry from the curated MCP server catalog."""

    return [McpRegistryEntry(**e) for e in mcp_registry_service.list_registry()]


@catalog_router.get("/{key}", response_model=McpRegistryEntry)
async def get_registry_entry(key: str) -> McpRegistryEntry:
    entry = mcp_registry_service.get_registry_entry(key)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"unknown registry entry '{key}'"
        )
    return McpRegistryEntry(**entry)
