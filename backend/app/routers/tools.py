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
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.validation import require_user_credential
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    tool_not_found,
    unknown_tool_definition,
)
from app.models.tool import Tool
from app.schemas.tool import (
    ToolCreate,
    ToolDefinitionSchema,
    ToolInstanceResponse,
    ToolPatch,
    ToolRunRequest,
    ToolRunResponse,
)
from app.services import audit_service
from app.tools.registry import registry as tool_registry
from app.tools.runner import run_tool

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


async def _load_owned(db: AsyncSession, tool_id: uuid.UUID, user_id: uuid.UUID) -> Tool:
    row = (
        await db.execute(
            select(Tool).where(
                Tool.id == tool_id,
                # Either owned by the current user or a system-owned (NULL) tool.
                (Tool.user_id == user_id) | (Tool.user_id.is_(None)),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise tool_not_found()
    return row


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
    stmt = select(Tool).where((Tool.user_id == user.id) | (Tool.user_id.is_(None)))
    if definition_key is not None:
        stmt = stmt.where(Tool.definition_key == definition_key)
    if enabled is not None:
        stmt = stmt.where(Tool.enabled == enabled)
    rows = (await db.execute(stmt.order_by(Tool.created_at.desc()))).scalars().all()
    return [_to_response(r) for r in rows]


@crud_router.post("", response_model=ToolInstanceResponse, status_code=201)
async def create_tool(
    payload: ToolCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ToolInstanceResponse:
    if tool_registry.get(payload.definition_key) is None:
        raise HTTPException(
            status_code=400, detail=f"unknown definition '{payload.definition_key}'"
        )
    await require_user_credential(db, credential_id=payload.credential_id, user_id=user.id)

    tool = Tool(
        user_id=user.id,
        definition_key=payload.definition_key,
        name=payload.name,
        description=payload.description,
        parameters=payload.parameters,
        credential_id=payload.credential_id,
        enabled=payload.enabled,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="tool.create",
        target_type="tool",
        target_id=tool.id,
        target_name_snapshot=tool.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "definition_key": tool.definition_key,
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
    return _to_response(await _load_owned(db, tool_id, user.id))


@crud_router.patch("/{tool_id}", response_model=ToolInstanceResponse)
async def update_tool(
    tool_id: uuid.UUID,
    payload: ToolPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ToolInstanceResponse:
    tool = await _load_owned(db, tool_id, user.id)
    if payload.name is not None:
        tool.name = payload.name
    if payload.description is not None:
        tool.description = payload.description
    if payload.parameters is not None:
        tool.parameters = payload.parameters
    if payload.credential_id is not None or "credential_id" in payload.model_fields_set:
        await require_user_credential(db, credential_id=payload.credential_id, user_id=user.id)
        tool.credential_id = payload.credential_id
    if payload.enabled is not None:
        tool.enabled = payload.enabled
    await db.commit()
    await db.refresh(tool)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="tool.update",
        target_type="tool",
        target_id=tool.id,
        target_name_snapshot=tool.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "definition_key": tool.definition_key,
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
    tool = await _load_owned(db, tool_id, user.id)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="tool.delete",
        target_type="tool",
        target_id=tool.id,
        target_name_snapshot=tool.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={"definition_key": tool.definition_key},
    )
    await db.delete(tool)
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
    tool = await _load_owned(db, tool_id, user.id)
    runtime_args = payload.runtime_args if payload else {}
    result = await run_tool(
        db=db,
        tool=tool,
        registry=tool_registry,
        runtime_args=runtime_args,
    )
    if result.success:
        from datetime import UTC
        from datetime import datetime as _dt

        tool.last_used_at = _dt.now(UTC).replace(tzinfo=None)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="tool.run",
        target_type="tool",
        target_id=tool.id,
        target_name_snapshot=tool.name,
        target_owner_user_id=user.id,
        outcome="success" if result.success else "failure",
        reason_code=None if result.success else "tool_run_failed",
        reason_message=result.error,
        request=request,
        metadata={
            "definition_key": tool.definition_key,
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
