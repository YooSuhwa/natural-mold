"""MCP server / tool API.

DB access and side effects live in :mod:`app.services.mcp_service` (BE-S2);
this router keeps schema conversion, ``Depends`` guards, and commits.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import registrations
from app.authz import registration, tool_blocks
from app.authz.audit_actions import MCP_TOOL_BLOCK_UPDATED, TOOL_APPROVAL_DEFAULT_UPDATED
from app.authz.delegation_sweep import revalidate_object
from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, CAP, CATALOG, RES, authz_policy
from app.authz.registration_state import effective_state
from app.authz.resource_gate import gate_cap
from app.authz.resource_lifecycle import (
    LifecycleChange,
    apply_lifecycle,
    on_resource_created,
    on_resource_deleted,
)
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.credentials.validation import require_credential_use
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import (
    credential_not_found,
    unknown_registry_entry,
)
from app.mcp import discovery, review
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import connect_and_list
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.schemas.approval import RegistrationState, RegistrationStatus
from app.schemas.credential_binding import CredentialMode
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
    McpToolBlockCreate,
    McpToolBlockList,
    McpToolBlockView,
    McpToolPendingVersion,
    McpToolResponse,
    McpToolReviewRequest,
    McpToolWithServerResponse,
)
from app.schemas.tool_approval import ApprovalDefaultUpdate, default_change
from app.services import mcp_registry as mcp_registry_service
from app.services import mcp_service
from app.services.mcp_server_view import (
    export_names,
    masked,
    secret_readers,
    visible_args,
    visible_url,
)

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp"])
# Catalog of well-known MCP servers — separate prefix so router mount paths
# stay parallel to the credentials catalog (``/api/credential-types``).
catalog_router = APIRouter(prefix="/api/mcp-server-types", tags=["mcp"])


# -- Helpers -----------------------------------------------------------------


def _server_to_response(server: McpServer, readable: bool = True) -> McpServerResponse:
    """``readable=False`` keeps header/env keys but masks their values (non-editors)."""
    return McpServerResponse(
        id=server.id,
        user_id=server.user_id,
        name=server.name,
        description=server.description,
        transport=server.transport,
        url=visible_url(server, readable),
        command=server.command,
        args=visible_args(server, readable),
        env_vars=(server.env_vars or {}) if readable else masked(server.env_vars),
        headers=(server.headers or {}) if readable else masked(server.headers),
        credential_id=server.credential_id,
        credential_mode=cast("CredentialMode", server.credential_mode or "none"),
        credential_definition_key=server.credential_definition_key,
        status=server.status,
        last_pinged_at=server.last_pinged_at,
        last_tool_count=server.last_tool_count,
        last_error=server.last_error,
        is_system=bool(getattr(server, "is_system", False)),
        health_status=getattr(server, "health_status", None),
        health_polled_at=getattr(server, "health_polled_at", None),
        health_message=getattr(server, "health_message", None),
        approval_default=server.approval_default,
        registration_state=cast("RegistrationState", effective_state(server)),
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _merged(changes: list[LifecycleChange]) -> LifecycleChange:
    return LifecycleChange(tuple(i for change in changes for i in change.outbox_ids))


def _tool_to_response(tool: McpTool, server_url: str | None = None) -> McpToolResponse:
    pending = review.pending_view(tool, server_url)
    return McpToolResponse(
        id=tool.id,
        server_id=tool.server_id,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema or {},
        enabled=tool.enabled,
        review_state=tool.review_state,  # type: ignore[arg-type]  # CHECK-constrained
        pending_version=McpToolPendingVersion(**pending) if pending else None,
        reviewed_at=tool.reviewed_at,
        approval_default=tool.approval_default,
        last_seen_at=getattr(tool, "last_seen_at", None),
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


# -- CRUD --------------------------------------------------------------------


@router.get("", response_model=list[McpServerResponse])
@authz_policy(CANDIDATES("mcp_server", "can_use"))
async def list_servers(
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[McpServerResponse]:
    rows = await mcp_service.list_servers(db, context, authz=authz)
    readers = await secret_readers(authz, context, rows)
    return [_server_to_response(r, r.id in readers) for r in rows]


@router.post("", response_model=McpServerResponse, status_code=201)
@authz_policy(CAP("cap_register_mcp"))
async def create_server(
    payload: McpServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpServerResponse:
    await gate_cap(authz, context, "cap_register_mcp")
    server = await mcp_service.create_server(db, context=context, data=payload, authz=authz)
    change = await on_resource_created(db, server, mode=authz.mode)
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
    await apply_lifecycle(change, authz)  # read-your-writes before responding (8.6)
    await mcp_service.invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.post("/from-registry", response_model=McpServerResponse, status_code=201)
@authz_policy(CAP("cap_register_mcp"))
async def create_server_from_registry(
    payload: McpServerCreateFromRegistry,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpServerResponse:
    """Create a server from a curated catalog entry.

    Pre-fills transport / URL / stdio command / env_var template from the
    registry, then sticks the user-supplied name and (optional)
    ``credential_id`` on top. Unknown ``registry_key`` is a 400 — the
    catalog is the source of truth.
    """

    await gate_cap(authz, context, "cap_register_mcp")
    entry = mcp_registry_service.get_registry_entry(payload.registry_key)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown registry entry '{payload.registry_key}'",
        )

    server = await mcp_service.create_server_from_registry_entry(
        db,
        context=context,
        name=payload.name,
        credential_id=payload.credential_id,
        entry=entry,
        authz=authz,
    )
    change = await on_resource_created(db, server, mode=authz.mode)
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
    await apply_lifecycle(change, authz)
    await mcp_service.invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.post("/probe", response_model=McpProbeResponse)
@authz_policy(CAP("cap_register_mcp"))
async def probe_mcp_server(
    payload: McpProbeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpProbeResponse:
    """Connect to an MCP server without persisting it.

    Powers the wizard's "preview before commit" flow — returns the same
    tool descriptors the regular discover path would import, so the UI can
    render results identically. No DB writes happen here, so a user who
    abandons the wizard leaves no orphan rows.
    """

    await gate_cap(authz, context, "cap_register_mcp")
    await registration.admit(db, context, "mcp_server")
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
        attached = await require_credential_use(
            db,
            credential_id=payload.credential_id,
            context=context,
            authz=authz,
            allow_company=True,
        )
        auth = await resolve_mcp_auth(
            db,
            credential_id=payload.credential_id,
            # A company shared credential has no owner account.
            user_id=user.id if attached is None or attached.user_id is not None else None,
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
        tenant_id=context.tenant_id,
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
@authz_policy(CANDIDATES("mcp_server", "can_use"))
async def list_all_user_mcp_tools(
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[McpToolWithServerResponse]:
    """All MCP tools across the user's servers — powers the unified Tools
    picker (Tools tab → MCP source) so the user can bind individual tools
    to an agent without first navigating to each server detail page."""

    rows = await mcp_service.list_all_tools(db, context, authz=authz)
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
@authz_policy(CAP("cap_register_mcp"))
async def import_servers(
    payload: McpImportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
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

    await gate_cap(authz, context, "cap_register_mcp")
    created: list[McpServer] = []
    result = await mcp_service.import_servers(db, context=context, data=payload, created=created)
    changes = [await on_resource_created(db, server, mode=authz.mode) for server in created]
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
    await apply_lifecycle(_merged(changes), authz)
    await mcp_service.invalidate_runtime_mcp_cache()
    return result


@router.get("/export", response_model=McpExportResponse)
@authz_policy(CANDIDATES("mcp_server", "can_view"))
async def export_servers(
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> McpExportResponse:
    """Dump every server owned by the caller in import-compatible shape.

    Secrets are never inlined — ``env`` / ``headers`` keep their pre-resolution
    template strings, and ``credential_id`` is exported as a bare reference.
    """

    rows = await mcp_service.list_servers(
        db, context, order_by_name=True, authz=authz, permission="can_view"
    )

    readers = await secret_readers(authz, context, rows)
    names = await export_names(db, context, rows)
    out: dict[str, McpExportEntry] = {}
    for row in rows:
        readable = row.id in readers
        out[names[row.id]] = McpExportEntry(
            transport=row.transport,
            command=row.command,
            args=visible_args(row, readable),
            env=dict(row.env_vars or {}) if readable else masked(row.env_vars),
            url=visible_url(row, readable),
            headers=dict(row.headers or {}) if readable else masked(row.headers),
            credential_id=row.credential_id,
            description=row.description,
        )
    return McpExportResponse(mcpServers=out)


@router.get("/{server_id}", response_model=McpServerDetailResponse)
@authz_policy(RES("mcp_server", "can_view", param="server_id", min_visible="can_use"))
async def get_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> McpServerDetailResponse:
    server = await mcp_service.load_owned(db, server_id, context, authz=authz)
    tools = await mcp_service.load_tools_for(db, server_id, context, authz=authz)
    base = _server_to_response(server, server.id in await secret_readers(authz, context, [server]))
    return McpServerDetailResponse(
        **base.model_dump(),
        tools=[_tool_to_response(t, server.url) for t in tools],
    )


@router.patch("/{server_id}", response_model=McpServerResponse)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def update_server(
    server_id: uuid.UUID,
    payload: McpServerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpServerResponse:
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    fields_set = payload.model_fields_set
    await mcp_service.update_server(db, server=server, context=context, data=payload, authz=authz)
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
@authz_policy(RES("mcp_server", "can_delete", param="server_id", min_visible="can_use"))
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> None:
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_delete"
    )
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action="mcp_server.delete",
        server=server,
    )
    change = await on_resource_deleted(db, server, mode=authz.mode)
    await mcp_service.delete_server(db, server=server, context=context, authz=authz)
    await db.commit()
    await apply_lifecycle(change, authz)
    await mcp_service.invalidate_runtime_mcp_cache()


# -- Connectivity probes ------------------------------------------------------


@router.post("/{server_id}/test", response_model=McpTestResponse)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def test_server(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpTestResponse:
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
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
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def discover_server_tools(
    server_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpDiscoverResponse:
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    probe, tools = await discovery.discover_tools(db, server)
    # Upserted tools (re)queue their server tuple; tuple writes are idempotent.
    discovered = _merged([await on_resource_created(db, t, mode=authz.mode) for t in tools])
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
    await apply_lifecycle(discovered, authz)
    await mcp_service.invalidate_runtime_mcp_cache()
    return McpDiscoverResponse(
        success=probe["success"],
        status=server.status,
        tools=[_tool_to_response(t, server.url) for t in tools],
        error=probe.get("error"),
    )


@router.post("/{server_id}/tools/{tool_id}/review", response_model=McpToolResponse)
@authz_policy(RES("mcp_server", "can_share", param="server_id", min_visible="can_use"))
async def review_server_tool(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: McpToolReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpToolResponse:
    """Approve or reject the tool version waiting for review (D11). Server managers only.

    A server registered under review is the administrators' to review (plan G10): its
    manager is the member whose registration is being checked.
    """
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_share"
    )
    await mcp_service.require_tool_reviewer(db, server, context)
    tool = await mcp_service.review_tool(
        db,
        server=server,
        tool_id=tool_id,
        decision=body.decision,
        fingerprint=body.fingerprint,
        user=user,
        request=request,
    )
    await db.commit()
    await db.refresh(tool)
    await mcp_service.invalidate_runtime_mcp_cache()
    return _tool_to_response(tool, server.url)


# -- Registration review (tool-governance-plan 3.7) ---------------------------


@router.get("/{server_id}/registration", response_model=RegistrationStatus)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def get_server_registration(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> RegistrationStatus:
    """Whether the server waits for an administrator, and the last answer."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    return RegistrationStatus.model_validate(await registrations.status(db, server))


@router.post("/{server_id}/registration", response_model=RegistrationStatus)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def request_server_registration(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> RegistrationStatus:
    """Ask the administrators again after a rejection or an expiry. Server editors only."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    await registrations.request_again(db, context, server)
    await db.commit()
    await db.refresh(server)
    await mcp_service.invalidate_runtime_mcp_cache()
    return RegistrationStatus.model_validate(await registrations.status(db, server))


# -- Approval defaults (tool-governance-plan 3.3) -----------------------------


@router.put("/{server_id}/approval-default", response_model=McpServerResponse)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def set_server_approval_default(
    server_id: uuid.UUID,
    body: ApprovalDefaultUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpServerResponse:
    """The default of every tool of this server that sets none of its own. Managers only."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    before, server.approval_default = server.approval_default, body.stored()
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action=TOOL_APPROVAL_DEFAULT_UPDATED,
        server=server,
        metadata={"scope": "mcp_server", **default_change(before, server.approval_default)},
    )
    await db.commit()
    await db.refresh(server)
    await mcp_service.invalidate_runtime_mcp_cache()
    return _server_to_response(server)


@router.put("/{server_id}/tools/{tool_id}/approval-default", response_model=McpToolResponse)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def set_server_tool_approval_default(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: ApprovalDefaultUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpToolResponse:
    """One MCP tool's own approval default (beats its server's). Server managers only."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_edit"
    )
    tool = await mcp_service.server_tool(db, server, tool_id)
    before, tool.approval_default = tool.approval_default, body.stored()
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action=TOOL_APPROVAL_DEFAULT_UPDATED,
        server=server,
        metadata={
            "scope": "mcp_tool",
            "mcp_tool_id": str(tool.id),
            "tool": tool.name,
            **default_change(before, tool.approval_default),
        },
    )
    await db.commit()
    await db.refresh(tool)
    await mcp_service.invalidate_runtime_mcp_cache()
    return _tool_to_response(tool, server.url)


# -- Registry catalog --------------------------------------------------------


@catalog_router.get("", response_model=list[McpRegistryEntry])
@authz_policy(CATALOG)
async def list_registry_entries() -> list[McpRegistryEntry]:
    """Return every entry from the curated MCP server catalog."""

    return [McpRegistryEntry(**e) for e in mcp_registry_service.list_registry()]


@catalog_router.get("/{key}", response_model=McpRegistryEntry)
@authz_policy(CATALOG)
async def get_registry_entry(key: str) -> McpRegistryEntry:
    entry = mcp_registry_service.get_registry_entry(key)
    if entry is None:
        raise unknown_registry_entry(key)
    return McpRegistryEntry(**entry)


# -- Blocking a tool for people or teams (tool-governance-plan 3.6) -----------


def _block_view(block: tool_blocks.Block) -> McpToolBlockView:
    return McpToolBlockView.model_validate(block, from_attributes=True)


@router.get("/{server_id}/tools/{tool_id}/blocks", response_model=McpToolBlockList)
@authz_policy(RES("mcp_server", "can_share", param="server_id", min_visible="can_use"))
async def list_server_tool_blocks(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> McpToolBlockList:
    """The people and teams kept from this tool. Server managers only."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_share"
    )
    tool = await mcp_service.server_tool(db, server, tool_id)
    blocks = await tool_blocks.list_blocks(db, context, tool)
    return McpToolBlockList(items=[_block_view(block) for block in blocks])


@router.post(
    "/{server_id}/tools/{tool_id}/blocks", response_model=McpToolBlockView, status_code=201
)
@authz_policy(RES("mcp_server", "can_share", param="server_id", min_visible="can_use"))
async def block_server_tool(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: McpToolBlockCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> McpToolBlockView:
    """Keep one person or team of the organization from this tool. Server managers only.

    Their runs stop being given the tool and a call already under way is refused.
    """
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_share"
    )
    tool = await mcp_service.server_tool(db, server, tool_id)
    change = await tool_blocks.block(
        db, context, server, tool, body.principal_type, body.principal_id
    )
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action=MCP_TOOL_BLOCK_UPDATED,
        server=server,
        metadata=_block_metadata(
            tool, change.grant.principal_type, change.grant.principal_id, True
        ),
    )
    grant_id = change.grant.id
    await db.commit()
    await _blocks_changed(authz, context, tool.id, change.outbox_ids)
    blocks = await tool_blocks.list_blocks(db, context, tool)
    return _block_view(next(block for block in blocks if block.grant_id == grant_id))


@router.delete("/{server_id}/tools/{tool_id}/blocks/{grant_id}", status_code=204)
@authz_policy(RES("mcp_server", "can_share", param="server_id", min_visible="can_use"))
async def unblock_server_tool(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> None:
    """Lift one block of this tool. Server managers only."""
    server = await mcp_service.load_owned(
        db, server_id, context, authz=authz, permission="can_share"
    )
    tool = await mcp_service.server_tool(db, server, tool_id)
    change = await tool_blocks.unblock(db, context, tool, grant_id)
    await mcp_service.record_server_audit(
        db,
        user=user,
        request=request,
        action=MCP_TOOL_BLOCK_UPDATED,
        server=server,
        metadata=_block_metadata(
            tool, change.grant.principal_type, change.grant.principal_id, False
        ),
    )
    await db.commit()
    await _blocks_changed(authz, context, tool.id, change.outbox_ids)


def _block_metadata(
    tool: McpTool, principal_type: str, principal_id: uuid.UUID, blocked: bool
) -> dict[str, Any]:
    return {
        "mcp_tool_id": str(tool.id),
        "tool": tool.name,
        "principal_type": principal_type,
        "principal_id": str(principal_id),
        "blocked": blocked,
    }


async def _blocks_changed(
    authz: AuthzService, context: OrgContext, tool_id: uuid.UUID, outbox_ids: tuple[uuid.UUID, ...]
) -> None:
    """After the commit: push the tuples, re-check delegations and drop cached tools."""
    await apply_lifecycle(LifecycleChange(outbox_ids), authz)
    await revalidate_object(
        authz, context.org_id, context.tenant_id, ("mcp_tool", tool_id), trigger="mcp_tool_block"
    )
    await mcp_service.invalidate_runtime_mcp_cache()
