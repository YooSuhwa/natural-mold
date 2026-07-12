"""MCP server / tool API.

DB access and side effects live in :mod:`app.services.mcp_service` (BE-S2);
this router keeps schema conversion, ``Depends`` guards, and commits.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    credential_not_found,
    unknown_registry_entry,
)
from app.mcp import discovery
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import connect_and_list
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.schemas.mcp import (
    McpDiscoverResponse,
    McpExportEntry,
    McpExportResponse,
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
from app.services import mcp_registry as mcp_registry_service
from app.services import mcp_service

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


# -- CRUD --------------------------------------------------------------------


@router.get("", response_model=list[McpServerResponse])
async def list_servers(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[McpServerResponse]:
    rows = await mcp_service.list_servers(db, user.id)
    return [_server_to_response(r) for r in rows]


@router.post("", response_model=McpServerResponse, status_code=201)
async def create_server(
    payload: McpServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpServerResponse:
    server = await mcp_service.create_server(db, user_id=user.id, data=payload)
    await db.commit()
    await db.refresh(server)
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.create",
        server=server,
    )
    await db.commit()
    await mcp_service.invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.post("/from-registry", response_model=McpServerResponse, status_code=201)
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

    server = await mcp_service.create_server_from_registry_entry(
        db,
        user_id=user.id,
        name=payload.name,
        credential_id=payload.credential_id,
        entry=entry,
    )
    await db.commit()
    await db.refresh(server)
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.create",
        server=server,
        metadata={"registry_key": payload.registry_key},
    )
    await db.commit()
    await mcp_service.invalidate_runtime_mcp_cache()
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
    mcp_service.validate_payload_consistency(transport, url, payload.command)

    credentials: dict[str, Any] | None = None
    if payload.credential_id is not None:
        auth = await resolve_mcp_auth(
            db,
            credential_id=payload.credential_id,
            user_id=user.id,
            static_headers=headers,
        )
        if auth.error:
            if auth.status == "credential_not_found":
                raise credential_not_found()
            return McpProbeResponse(
                success=False,
                server_info={},
                tools=[],
                error=auth.error,
            )
        if auth.credentials is None:
            raise credential_not_found()
        credentials = auth.credentials
        headers = auth.headers

    result = await connect_and_list(
        transport=transport,
        url=url,
        headers=headers,
        credentials=credentials,
        user_id=user.id,
        credential_id=payload.credential_id,
    )
    await mcp_service.record_probe_audit(
        db,
        user=user,
        request=request,
        registry_key=payload.registry_key,
        transport=transport,
        url=url,
        command_present=bool(payload.command),
        headers=headers,
        credential_bound=payload.credential_id is not None,
        result=result,
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

    rows = await mcp_service.list_all_tools(db, user.id)
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

    result = await mcp_service.import_servers(db, user_id=user.id, data=payload)
    await db.commit()
    await mcp_service.record_import_audit(
        db,
        user=user,
        request=request,
        result=result,
        entry_count=len(payload.mcpServers),
        overwrite=payload.overwrite,
    )
    await db.commit()
    await mcp_service.invalidate_runtime_mcp_cache()
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

    rows = await mcp_service.list_servers(db, user.id, order_by_name=True)

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
    server = await mcp_service.load_owned(db, server_id, user.id)
    tools = await mcp_service.load_tools_for(db, server_id)
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
    server = await mcp_service.load_owned(db, server_id, user.id)
    fields_set = payload.model_fields_set
    await mcp_service.update_server(db, server=server, user_id=user.id, data=payload)
    await db.commit()
    await db.refresh(server)
    await mcp_service.record_server_audit(
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
    await mcp_service.invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    server = await mcp_service.load_owned(db, server_id, user.id)
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.delete",
        server=server,
    )
    await mcp_service.delete_server(db, server=server)
    await db.commit()
    await mcp_service.invalidate_runtime_mcp_cache()


# -- Connectivity probes ------------------------------------------------------


@router.post("/{server_id}/test", response_model=McpTestResponse)
async def test_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> McpTestResponse:
    server = await mcp_service.load_owned(db, server_id, user.id)
    probe: dict[str, Any] = await discovery.test_server(db, server)
    await mcp_service.record_server_audit(
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
    await mcp_service.invalidate_runtime_mcp_cache()
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
    server = await mcp_service.load_owned(db, server_id, user.id)
    probe, tools = await discovery.discover_tools(db, server)
    await mcp_service.record_server_audit(
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
    await mcp_service.invalidate_runtime_mcp_cache()
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
        raise unknown_registry_entry(key)
    return McpRegistryEntry(**entry)
