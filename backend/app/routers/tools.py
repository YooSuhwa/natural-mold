from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.tool import MCPServerCreate, MCPServerResponse, ToolCustomCreate, ToolResponse
from app.services import tool_service

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=list[ToolResponse])
async def list_tools(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await tool_service.list_tools(db, user.id)


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


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    deleted = await tool_service.delete_tool(db, tool_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tool not found")
