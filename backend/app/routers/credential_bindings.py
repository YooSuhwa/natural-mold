"""Per-user connections and credential modes (plan 9.x, 11.3, S23, S28; F-04).

- ``GET    /api/credential-bindings``                         my connections (current org)
- ``PUT    /api/credential-bindings/{resource_type}/{id}``    connect my credential
- ``DELETE /api/credential-bindings/{resource_type}/{id}``    disconnect
- ``PUT    /api/tools/{tool_id}/credential-mode``             none | shared | per_user
- ``PUT    /api/mcp-servers/{server_id}/credential-mode``     same for an MCP server

The mode switch is a sub-resource rather than a ``PATCH /api/tools/{id}`` field (the plan's
S28 wording) to keep the tool and MCP routers within their size budget.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, SELF, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.credential_binding import (
    BindingResourceType,
    CredentialBindingIn,
    CredentialBindingOut,
    CredentialModeIn,
    CredentialModeOut,
)
from app.services import credential_binding_service as bindings
from app.services import credential_mode_service as modes

router = APIRouter(prefix="/api/credential-bindings", tags=["credential-bindings"])
mode_router = APIRouter(tags=["credential-bindings"])


@router.get("", response_model=list[CredentialBindingOut])
@authz_policy(SELF)
async def list_credential_bindings(
    resource_type: BindingResourceType | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> list[CredentialBindingOut]:
    rows = await bindings.list_mine(db, context, resource_type)
    return [CredentialBindingOut.model_validate(row) for row in rows]


@router.put("/{resource_type}/{resource_id}", response_model=CredentialBindingOut)
@authz_policy(SELF)
async def put_credential_binding(
    resource_type: BindingResourceType,
    resource_id: uuid.UUID,
    body: CredentialBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> CredentialBindingOut:
    row = await bindings.bind(
        db,
        context,
        authz,
        resource_type=resource_type,
        resource_id=resource_id,
        credential_id=body.credential_id,
        request=request,
    )
    await db.commit()
    await db.refresh(row)
    return CredentialBindingOut.model_validate(row)


@router.delete("/{resource_type}/{resource_id}", status_code=204)
@authz_policy(SELF)
async def delete_credential_binding(
    resource_type: BindingResourceType,
    resource_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await bindings.unbind(
        db, context, resource_type=resource_type, resource_id=resource_id, request=request
    )
    await db.commit()
    return Response(status_code=204)


async def _switch(
    db: AsyncSession,
    context: OrgContext,
    authz: AuthzService,
    kind: str,
    resource_id: uuid.UUID,
    payload: CredentialModeIn,
    request: Request,
) -> CredentialModeOut:
    row = await bindings.load_target(db, context, authz, kind, resource_id, "can_edit")
    result = await modes.set_mode(db, context, authz, row, payload, request=request)
    await db.commit()
    return result


@mode_router.put("/api/tools/{tool_id}/credential-mode", response_model=CredentialModeOut)
@authz_policy(RES("tool", "can_edit", param="tool_id", min_visible="can_use"))
async def put_tool_credential_mode(
    tool_id: uuid.UUID,
    payload: CredentialModeIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> CredentialModeOut:
    return await _switch(db, context, authz, "tool", tool_id, payload, request)


@mode_router.put("/api/mcp-servers/{server_id}/credential-mode", response_model=CredentialModeOut)
@authz_policy(RES("mcp_server", "can_edit", param="server_id", min_visible="can_use"))
async def put_mcp_server_credential_mode(
    server_id: uuid.UUID,
    payload: CredentialModeIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> CredentialModeOut:
    return await _switch(db, context, authz, "mcp_server", server_id, payload, request)
