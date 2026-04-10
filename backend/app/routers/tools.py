from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import mcp_server_not_found, tool_not_found
from app.schemas.tool import (
    MCPServerCreate,
    MCPServerResponse,
    ToolAuthConfigUpdate,
    ToolCustomCreate,
    ToolResponse,
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


@router.post("/mcp-server", response_model=MCPServerResponse, status_code=201)
async def register_mcp_server(
    data: MCPServerCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await tool_service.register_mcp_server(db, data, user.id)


@router.post("/mcp-server/{server_id}/test")
async def test_mcp_connection(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    from sqlalchemy import select

    from app.agent_runtime.mcp_client import test_mcp_connection as mcp_test
    from app.models.tool import MCPServer

    result = await db.execute(
        select(MCPServer).where(MCPServer.id == server_id, MCPServer.user_id == user.id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise mcp_server_not_found()

    test_result = await mcp_test(server.url, server.auth_config)
    return test_result


@router.patch("/{tool_id}/auth-config", response_model=ToolResponse)
async def update_tool_auth_config(
    tool_id: uuid.UUID,
    data: ToolAuthConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    tool = await tool_service.update_tool_auth_config(db, tool_id, data.auth_config)
    if not tool:
        raise tool_not_found()
    return tool


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    deleted = await tool_service.delete_tool(db, tool_id, user.id)
    if not deleted:
        raise tool_not_found()
