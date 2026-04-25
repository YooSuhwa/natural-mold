from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import tool_not_found
from app.schemas.tool import (
    ToolCustomCreate,
    ToolResponse,
    ToolUpdate,
)
from app.services import tool_service

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=list[ToolResponse])
async def list_tools(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    tools = await tool_service.list_tools(db, user.id)
    tool_ids = [t.id for t in tools]
    counts = await tool_service.get_tool_agent_counts(db, tool_ids)
    responses = []
    for t in tools:
        resp = ToolResponse.model_validate(t)
        resp.agent_count = counts.get(t.id, 0)
        responses.append(resp)
    return responses


@router.post("/custom", response_model=ToolResponse, status_code=201)
async def create_custom_tool(
    data: ToolCustomCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await tool_service.create_custom_tool(db, data, user.id)


@router.post("/{tool_id}/test")
async def test_tool_connection(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Probe an MCP tool's connection (M6.1 옵션 D).

    tool.connection 경유로 url/auth를 해석하므로 chat runtime과 동일한 경로를
    공유한다. PREBUILT/CUSTOM 또는 connection 미바인딩 시 400.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.agent_runtime.mcp_client import test_mcp_connection as mcp_test
    from app.models.connection import Connection
    from app.models.tool import Tool
    from app.schemas.tool import ToolType
    from app.services.env_var_resolver import ToolConfigError, resolve_env_vars

    result = await db.execute(
        select(Tool)
        .where(Tool.id == tool_id)
        .options(selectinload(Tool.connection).selectinload(Connection.credential))
    )
    tool = result.scalar_one_or_none()
    if tool is None:
        raise tool_not_found()
    if not tool.is_system and tool.user_id != user.id:
        raise tool_not_found()
    if tool.type != ToolType.MCP:
        raise ToolConfigError(
            f"Tool '{tool.name}' is type={tool.type}; /test only supports MCP tools."
        )
    if tool.connection_id is None or tool.connection is None:
        raise ToolConfigError(
            f"MCP tool '{tool.name}' has no connection — bind one via "
            "PATCH /api/tools/{tool_id} before testing."
        )

    conn = tool.connection
    extra = conn.extra_config or {}
    url = extra.get("url")
    if not url:
        raise ToolConfigError(
            f"MCP tool '{tool.name}' connection {conn.id} is missing extra_config.url"
        )
    from app.agent_runtime.mcp_client import extract_transport_headers

    effective_auth = resolve_env_vars(
        extra.get("env_vars"),
        conn.credential,
        context={"connection_id": str(conn.id), "tool_name": tool.name},
    )
    return await mcp_test(
        url, effective_auth, extra_headers=extract_transport_headers(extra)
    )


@router.patch("/{tool_id}", response_model=ToolResponse)
async def update_tool_endpoint(
    tool_id: uuid.UUID,
    payload: ToolUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await tool_service.update_tool(db, tool_id, user.id, payload)


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    deleted = await tool_service.delete_tool(db, tool_id, user.id)
    if not deleted:
        raise tool_not_found()
