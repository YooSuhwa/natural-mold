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

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
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
async def list_tool_types() -> list[ToolDefinitionSchema]:
    return [ToolDefinitionSchema(**d.serialize()) for d in tool_registry.all()]


@catalog_router.get("/{key}", response_model=ToolDefinitionSchema)
async def get_tool_type(key: str) -> ToolDefinitionSchema:
    definition = tool_registry.get(key)
    if definition is None:
        raise unknown_tool_definition(key)
    return ToolDefinitionSchema(**definition.serialize())


# -- CRUD --------------------------------------------------------------------


@crud_router.get("", response_model=list[ToolInstanceResponse])
async def list_tools(
    definition_key: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[ToolInstanceResponse]:
    rows = await tool_service.list_tools(
        db, user.id, definition_key=definition_key, enabled=enabled
    )
    return [_to_response(r) for r in rows]


@crud_router.post("", response_model=ToolInstanceResponse, status_code=201)
async def create_tool(
    payload: ToolCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ToolInstanceResponse:
    tool = await tool_service.create_tool(db, user_id=user.id, data=payload)
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
    return _to_response(tool)


@crud_router.get("/{tool_id}", response_model=ToolInstanceResponse)
async def get_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ToolInstanceResponse:
    return _to_response(await tool_service.load_owned(db, tool_id, user.id))


@crud_router.patch("/{tool_id}", response_model=ToolInstanceResponse)
async def update_tool(
    tool_id: uuid.UUID,
    payload: ToolPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ToolInstanceResponse:
    tool = await tool_service.load_owned(db, tool_id, user.id)
    await tool_service.update_tool(db, tool=tool, user_id=user.id, data=payload)
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
async def delete_tool(
    tool_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    tool = await tool_service.load_owned(db, tool_id, user.id)
    await tool_service.record_tool_audit(
        db,
        user=user,
        request=request,
        action="tool.delete",
        tool=tool,
    )
    await tool_service.delete_tool(db, tool=tool)
    await db.commit()


# -- Run ---------------------------------------------------------------------


@crud_router.post("/{tool_id}/run", response_model=ToolRunResponse)
async def run_tool_endpoint(
    tool_id: uuid.UUID,
    request: Request,
    payload: ToolRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ToolRunResponse:
    tool = await tool_service.load_owned(db, tool_id, user.id)
    runtime_args = payload.runtime_args if payload else {}
    result = await tool_service.run_tool_instance(db, tool=tool, runtime_args=runtime_args)
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
