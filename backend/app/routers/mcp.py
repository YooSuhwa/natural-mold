"""MCP server / tool API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.dependencies import CurrentUser, get_current_user, get_db
from app.mcp import discovery
from app.mcp.client import connect_and_list
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.schemas.mcp import (
    McpDiscoverResponse,
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> McpServerResponse:
    _validate_payload_consistency(payload.transport, payload.url, payload.command)
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
    return _server_to_response(server)


@router.post(
    "/from-registry", response_model=McpServerResponse, status_code=201
)
async def create_server_from_registry(
    payload: McpServerCreateFromRegistry,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
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
    return _server_to_response(server)


@router.post("/probe", response_model=McpProbeResponse)
async def probe_mcp_server(
    payload: McpProbeRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
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
        server.credential_id = payload.credential_id
    if "status" in fields_set and payload.status is not None:
        server.status = payload.status

    _validate_payload_consistency(server.transport, server.url, server.command)
    await db.commit()
    await db.refresh(server)
    return _server_to_response(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    server = await _load_owned(db, server_id, user.id)
    # Manual cascade for SQLite tests where ondelete=CASCADE isn't enforced.
    for row in await _load_tools_for(db, server_id):
        await db.delete(row)
    await db.delete(server)
    await db.commit()


# -- Connectivity probes ------------------------------------------------------


@router.post("/{server_id}/test", response_model=McpTestResponse)
async def test_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> McpTestResponse:
    server = await _load_owned(db, server_id, user.id)
    probe: dict[str, Any] = await discovery.test_server(db, server)
    await db.commit()
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> McpDiscoverResponse:
    server = await _load_owned(db, server_id, user.id)
    probe, tools = await discovery.discover_tools(db, server)
    await db.commit()
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
