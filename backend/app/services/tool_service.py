from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tool import MCPServer, Tool
from app.schemas.tool import MCPServerCreate, ToolCustomCreate


async def list_tools(db: AsyncSession, user_id: uuid.UUID) -> list[Tool]:
    result = await db.execute(
        select(Tool)
        .where(or_(Tool.user_id == user_id, Tool.is_system.is_(True)))
        .order_by(Tool.is_system.desc(), Tool.created_at.desc())
    )
    return list(result.scalars().all())


async def create_custom_tool(db: AsyncSession, data: ToolCustomCreate, user_id: uuid.UUID) -> Tool:
    tool = Tool(
        user_id=user_id,
        type="custom",
        name=data.name,
        description=data.description,
        api_url=data.api_url,
        http_method=data.http_method,
        parameters_schema=data.parameters_schema,
        auth_type=data.auth_type,
        auth_config=data.auth_config,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def register_mcp_server(
    db: AsyncSession, data: MCPServerCreate, user_id: uuid.UUID
) -> MCPServer:
    server = MCPServer(
        user_id=user_id,
        name=data.name,
        url=data.url,
        auth_type=data.auth_type,
        auth_config=data.auth_config,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server, ["tools"])
    return server


async def get_mcp_servers(db: AsyncSession, user_id: uuid.UUID) -> list[MCPServer]:
    result = await db.execute(
        select(MCPServer)
        .where(MCPServer.user_id == user_id)
        .options(selectinload(MCPServer.tools))
        .order_by(MCPServer.created_at.desc())
    )
    return list(result.scalars().all())


async def update_tool_auth_config(
    db: AsyncSession,
    tool_id: uuid.UUID,
    auth_config: dict,
) -> Tool | None:
    """Update auth_config for a prebuilt tool."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        return None
    if tool.type != "prebuilt":
        return None
    tool.auth_config = auth_config
    await db.commit()
    await db.refresh(tool)
    return tool


async def delete_tool(db: AsyncSession, tool_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        return False
    if tool.is_system:
        return False  # System tools cannot be deleted
    if tool.user_id != user_id:
        return False
    await db.delete(tool)
    await db.commit()
    return True
