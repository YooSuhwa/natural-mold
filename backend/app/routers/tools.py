"""Tool API — greenfield rewrite (M3).

Endpoints:
- ``GET    /api/tool-types``                 catalog of registered ToolDefinitions
- ``GET    /api/tool-types/{key}``           single definition
- ``GET    /api/tools``                      list user's tools
- ``POST   /api/tools``                      create
- ``GET    /api/tools/{id}``                 detail
- ``PATCH  /api/tools/{id}``                 update
- ``DELETE /api/tools/{id}``                 delete
- ``POST   /api/tools/{id}/run``             execute the tool with optional runtime args

DB access and side effects live in :mod:`app.services.tool_service` (BE-S2);
this router keeps schema conversion, ``Depends`` guards, and commits.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.personal_accounts import require_unshared_for_personal
from app.authz.policies import CANDIDATES, CAP, CATALOG, RES, authz_policy
from app.authz.resource_gate import gate_cap
from app.authz.resource_lifecycle import apply_lifecycle, on_resource_created, on_resource_deleted
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import unknown_tool_definition
from app.models.tool import Tool
from app.schemas.tool import (
    ToolCreate,
    ToolDefinitionSchema,
    ToolInstanceResponse,
    ToolPatch,
    ToolRunRequest,
    ToolRunResponse,
)
from app.services import tool_service
from app.tools.execution_scope import ToolRunTarget
from app.tools.registry import registry as tool_registry

router = APIRouter(tags=["tools"])

catalog_router = APIRouter(prefix="/api/tool-types", tags=["tools"])
crud_router = APIRouter(prefix="/api/tools", tags=["tools"])


# -- Helpers -----------------------------------------------------------------


def _to_response(tool: Tool) -> ToolInstanceResponse:
    return ToolInstanceResponse(
        id=tool.id,
        user_id=tool.user_id,
        definition_key=tool.definition_key,
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters or {},
        credential_id=tool.credential_id,
        enabled=tool.enabled,
        last_used_at=tool.last_used_at,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


# -- Catalog -----------------------------------------------------------------


@catalog_router.get("", response_model=list[ToolDefinitionSchema])
@authz_policy(CATALOG)
async def list_tool_types() -> list[ToolDefinitionSchema]:
    return [ToolDefinitionSchema(**d.serialize()) for d in tool_registry.all()]


@catalog_router.get("/{key}", response_model=ToolDefinitionSchema)
@authz_policy(CATALOG)
async def get_tool_type(key: str) -> ToolDefinitionSchema:
    definition = tool_registry.get(key)
    if definition is None:
        raise unknown_tool_definition(key)
    return ToolDefinitionSchema(**definition.serialize())


# -- CRUD --------------------------------------------------------------------


@crud_router.get("", response_model=list[ToolInstanceResponse])
@authz_policy(CANDIDATES("tool", "can_use"))
async def list_tools(
    definition_key: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[ToolInstanceResponse]:
    rows = await tool_service.list_tools(
        db, context, definition_key=definition_key, enabled=enabled, authz=authz
    )
    return [_to_response(r) for r in rows]


@crud_router.post("", response_model=ToolInstanceResponse, status_code=201)
@authz_policy(CAP("cap_create_tool"))
async def create_tool(
    payload: ToolCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> ToolInstanceResponse:
    await gate_cap(authz, context, "cap_create_tool")
    tool = await tool_service.create_tool(db, context=context, data=payload, authz=authz)
    change = await on_resource_created(db, tool, mode=authz.mode)
    await db.commit()
    await db.refresh(tool)
    await tool_service.record_tool_audit(
        db,
        user=user,
        request=request,
        action="tool.create",
        tool=tool,
        metadata={
            "parameter_keys": sorted((tool.parameters or {}).keys()),
            "credential_id": str(tool.credential_id) if tool.credential_id else None,
        },
    )
    await db.commit()
    await apply_lifecycle(change, authz)  # read-your-writes before responding (8.6)
    return _to_response(tool)


@crud_router.get("/{tool_id}", response_model=ToolInstanceResponse)
@authz_policy(RES("tool", "can_view", param="tool_id", min_visible="can_use"))
async def get_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> ToolInstanceResponse:
    return _to_response(await tool_service.load_owned(db, tool_id, context, authz=authz))


@crud_router.patch("/{tool_id}", response_model=ToolInstanceResponse)
@authz_policy(RES("tool", "can_edit", param="tool_id", min_visible="can_use"))
async def update_tool(
    tool_id: uuid.UUID,
    payload: ToolPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> ToolInstanceResponse:
    tool = await tool_service.load_owned(db, tool_id, context, authz=authz, permission="can_edit")
    await tool_service.update_tool(db, tool=tool, context=context, data=payload, authz=authz)
    if payload.credential_id is not None:
        await require_unshared_for_personal(db, context, tool)  # 10.4, E-05
    await db.commit()
    await db.refresh(tool)
    await tool_service.record_tool_audit(
        db,
        user=user,
        request=request,
        action="tool.update",
        tool=tool,
        metadata={
            "changed_fields": sorted(payload.model_fields_set),
            "credential_changed": "credential_id" in payload.model_fields_set,
        },
    )
    await db.commit()
    return _to_response(tool)


@crud_router.delete("/{tool_id}", status_code=204)
@authz_policy(RES("tool", "can_delete", param="tool_id", min_visible="can_use"))
async def delete_tool(
    tool_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> None:
    tool = await tool_service.load_owned(db, tool_id, context, authz=authz, permission="can_delete")
    await tool_service.record_tool_audit(
        db,
        user=user,
        request=request,
        action="tool.delete",
        tool=tool,
    )
    change = await on_resource_deleted(db, tool, mode=authz.mode)
    await tool_service.delete_tool(db, tool=tool, context=context, authz=authz)
    await db.commit()
    await apply_lifecycle(change, authz)


# -- Run ---------------------------------------------------------------------


@crud_router.post("/{tool_id}/run", response_model=ToolRunResponse)
@authz_policy(RES("tool", "can_edit", param="tool_id", min_visible="can_use"))
async def run_tool_endpoint(
    tool_id: uuid.UUID,
    request: Request,
    payload: ToolRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> ToolRunResponse:
    tool = await tool_service.load_owned(db, tool_id, context, authz=authz, permission="can_edit")
    runtime_args = payload.runtime_args if payload else {}
    result = await tool_service.run_tool_instance(
        db, ToolRunTarget(tool.id, context), runtime_args, authz
    )
    await tool_service.record_tool_audit(
        db,
        user=user,
        request=request,
        action="tool.run",
        tool=tool,
        outcome="success" if result.success else "failure",
        reason_code=None if result.success else "tool_run_failed",
        reason_message=result.error,
        metadata={
            "runtime_arg_keys": sorted((runtime_args or {}).keys()),
            "duration_ms": result.duration_ms,
            "http_status": result.http_status,
        },
    )
    await db.commit()
    return ToolRunResponse(**result.to_dict())


# -- Composition -------------------------------------------------------------

router.include_router(catalog_router)
router.include_router(crud_router)
