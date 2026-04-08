from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tool import AgentToolLink, MCPServer, Tool
from app.schemas.tool import MCPServerCreate, ToolCustomCreate


async def get_tools_catalog(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """사용 가능한 도구 카탈로그를 필요 컬럼만 조회한다.

    builder_service, read_tools 양쪽에서 공통으로 사용한다.
    """
    result = await db.execute(
        select(Tool.name, Tool.description, Tool.type).where(
            or_(Tool.user_id == user_id, Tool.is_system.is_(True))
        )
    )
    return [
        {"name": row.name, "description": row.description or "", "type": row.type}
        for row in result.all()
    ]


async def list_tools(db: AsyncSession, user_id: uuid.UUID) -> list[Tool]:
    result = await db.execute(
        select(Tool)
        .where(or_(Tool.user_id == user_id, Tool.is_system.is_(True)))
        .order_by(Tool.is_system.desc(), Tool.created_at.desc())
    )
    return list(result.scalars().all())


async def get_tool_agent_counts(
    db: AsyncSession, tool_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Get the number of agents using each tool."""
    if not tool_ids:
        return {}
    result = await db.execute(
        select(AgentToolLink.tool_id, func.count(AgentToolLink.agent_id))
        .where(AgentToolLink.tool_id.in_(tool_ids))
        .group_by(AgentToolLink.tool_id)
    )
    return {row[0]: row[1] for row in result.all()}


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
    # Discover tools BEFORE opening the DB transaction to avoid holding
    # a connection while waiting on an external HTTP call.
    from app.agent_runtime.mcp_client import list_mcp_tools

    try:
        mcp_tools = await list_mcp_tools(data.url)
    except Exception:
        mcp_tools = []

    server = MCPServer(
        user_id=user_id,
        name=data.name,
        url=data.url,
        auth_type=data.auth_type,
        auth_config=data.auth_config,
    )
    db.add(server)
    await db.flush()

    for mt in mcp_tools:
        tool = Tool(
            user_id=user_id,
            type="mcp",
            mcp_server_id=server.id,
            name=mt["name"],
            description=mt.get("description"),
            parameters_schema=mt.get("inputSchema"),
            auth_type=data.auth_type,
            auth_config=data.auth_config,
        )
        db.add(tool)

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
    if tool.type not in ("prebuilt", "mcp"):
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
