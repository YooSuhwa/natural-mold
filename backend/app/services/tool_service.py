from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tool import AgentToolLink, MCPServer, Tool
from app.schemas.tool import MCPServerCreate, ToolCustomCreate, ToolType
from app.services import connection_service, credential_service


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
    # `connection_id`가 있으면 이 값이 single source of truth.
    # `credential_id`는 connection.credential_id에서 파생 (split-brain 방지:
    # 클라이언트가 보낸 credential_id 값은 무시). `connection_id` 없는 legacy
    # 경로는 기존대로 credential_id만 검증.
    effective_credential_id: uuid.UUID | None = data.credential_id

    if data.connection_id:
        conn = await connection_service.validate_connection_for_custom_tool(
            db, data.connection_id, user_id
        )
        effective_credential_id = conn.credential_id

    if effective_credential_id:
        await credential_service.get_credential(db, effective_credential_id, user_id)

    tool = Tool(
        user_id=user_id,
        type=ToolType.CUSTOM,
        name=data.name,
        description=data.description,
        api_url=data.api_url,
        http_method=data.http_method,
        parameters_schema=data.parameters_schema,
        auth_type=data.auth_type,
        auth_config=data.auth_config,
        credential_id=effective_credential_id,
        connection_id=data.connection_id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def register_mcp_server(
    db: AsyncSession, data: MCPServerCreate, user_id: uuid.UUID
) -> MCPServer:
    if data.credential_id:
        await credential_service.get_credential(db, data.credential_id, user_id)

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
        credential_id=data.credential_id,
    )
    db.add(server)
    await db.flush()

    for mt in mcp_tools:
        tool = Tool(
            user_id=user_id,
            type=ToolType.MCP,
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


async def list_mcp_server_items(
    db: AsyncSession, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """List MCP servers as group items (server + tool_count + credential brief).

    Used by /api/tools/mcp-servers. Credential is eagerly loaded via an explicit
    selectinload (rather than relying on the relationship-level lazy="joined")
    so the SELECT shape stays predictable when combined with the tool_count
    subquery; tool_count is computed by a separate aggregated subquery joined
    on mcp_server_id.
    """
    tool_count_subq = (
        select(Tool.mcp_server_id, func.count(Tool.id).label("tool_count"))
        .where(Tool.mcp_server_id.is_not(None))
        .group_by(Tool.mcp_server_id)
        .subquery()
    )
    result = await db.execute(
        select(MCPServer, func.coalesce(tool_count_subq.c.tool_count, 0))
        .outerjoin(tool_count_subq, MCPServer.id == tool_count_subq.c.mcp_server_id)
        .where(MCPServer.user_id == user_id)
        .options(selectinload(MCPServer.credential))
        .order_by(MCPServer.created_at.desc())
    )
    items: list[dict[str, Any]] = []
    for server, tool_count in result.all():
        cred_brief = None
        if server.credential_id and server.credential:
            cred_brief = {
                "id": server.credential.id,
                "name": server.credential.name,
                "provider_name": server.credential.provider_name,
            }
        items.append(
            {
                "id": server.id,
                "name": server.name,
                "url": server.url,
                "auth_type": server.auth_type,
                "credential_id": server.credential_id,
                "credential": cred_brief,
                "status": server.status,
                "tool_count": int(tool_count or 0),
                "created_at": server.created_at,
            }
        )
    return items


async def _apply_credential_update(
    target: Tool | MCPServer,
    updates: dict[str, Any],
    db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    """Mutate target.credential_id from updates if present, verifying ownership."""
    if "credential_id" not in updates:
        return
    credential_id = updates["credential_id"]
    if credential_id is not None:
        await credential_service.get_credential(db, credential_id, user_id)
    target.credential_id = credential_id


async def update_mcp_server(
    db: AsyncSession,
    server_id: uuid.UUID,
    updates: dict[str, Any],
    user_id: uuid.UUID,
) -> MCPServer | None:
    """Partial update of an MCP server's name / credential / auth_config.

    Only fields present in ``updates`` are mutated. Returns None if the
    server does not exist or belongs to another user.
    """
    result = await db.execute(
        select(MCPServer).where(
            MCPServer.id == server_id, MCPServer.user_id == user_id
        )
    )
    server = result.scalar_one_or_none()
    if not server:
        return None

    await _apply_credential_update(server, updates, db, user_id)
    if "name" in updates and updates["name"] is not None:
        server.name = updates["name"]
    if "auth_config" in updates:
        # Replace semantics: passing {} clears the inline auth_config entirely.
        # Use credential_id for managed secrets; auth_config is the legacy
        # inline path and is overridden by credential at runtime.
        server.auth_config = updates["auth_config"]

    await db.commit()
    await db.refresh(server, ["tools"])
    return server


async def delete_mcp_server(
    db: AsyncSession, server_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Delete an MCP server. ORM cascade removes child tools.

    The selectinload is intentional: SQLAlchemy's ORM cascade requires the
    child collection to be loaded into the session for delete-orphan to fire.
    Removing this would silently leak orphaned Tool rows in async sessions.
    """
    result = await db.execute(
        select(MCPServer)
        .where(MCPServer.id == server_id, MCPServer.user_id == user_id)
        .options(selectinload(MCPServer.tools))
    )
    server = result.scalar_one_or_none()
    if not server:
        return False
    await db.delete(server)
    await db.commit()
    return True


async def update_tool_auth_config(
    db: AsyncSession,
    tool_id: uuid.UUID,
    updates: dict[str, Any],
    user_id: uuid.UUID,
) -> Tool | None:
    """Partial update of a tool's auth_config / credential_id.

    Only fields present in ``updates`` are mutated. For MCP and CUSTOM tools
    the caller must own the tool row. PREBUILT tools are shared
    (is_system=True) — mutation of shared rows remains a known limitation and
    will be addressed by per-user credential binding in a future change.
    """
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        return None
    if tool.type not in (ToolType.PREBUILT, ToolType.MCP, ToolType.CUSTOM):
        return None
    if tool.type in (ToolType.MCP, ToolType.CUSTOM) and tool.user_id != user_id:
        return None

    await _apply_credential_update(tool, updates, db, user_id)
    if "auth_config" in updates:
        # Replace semantics: passing {} clears existing inline auth.
        # All three auth dialogs (Prebuilt/Custom/MCPServer) intentionally send
        # {} alongside credential_id to wipe any legacy inline secret.
        tool.auth_config = updates["auth_config"]

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
