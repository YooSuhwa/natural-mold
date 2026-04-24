from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.error_codes import tool_not_found
from app.models.connection import Connection
from app.models.tool import AgentToolLink, Tool
from app.schemas.tool import ToolCustomCreate, ToolType, ToolUpdate
from app.services import connection_service


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
    # M6 이후 CUSTOM tool 생성은 connection_id 경유가 유일한 경로.
    # schema 단에서 required로 선언했지만 방어적으로 서비스에서도 확인한다.
    # connection ownership/active 검증은 chat runtime에서 일어나므로 여기선
    # 존재 + 소유 여부만 확인한다.
    await connection_service.validate_connection_for_custom_tool(
        db, data.connection_id, user_id
    )

    tool = Tool(
        user_id=user_id,
        type=ToolType.CUSTOM,
        name=data.name,
        description=data.description,
        api_url=data.api_url,
        http_method=data.http_method,
        parameters_schema=data.parameters_schema,
        auth_type=data.auth_type,
        connection_id=data.connection_id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def update_tool(
    db: AsyncSession,
    tool_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ToolUpdate,
) -> Tool:
    """PATCH /api/tools/{id} — connection_id 단일 필드 갱신 (M6.1 옵션 D).

    검증 순서:
    1) tool 존재 확인 (없으면 404)
    2) 소유권 확인 — system tool 또는 타 유저 tool은 404 (정보 노출 방지)
    3) PREBUILT는 (user_id, provider_name) 스코프이므로 PATCH 거부 (400)
    4) connection_id가 not None이면:
       - connection 존재 + 소유 확인 (없거나 타 유저면 404, IDOR 방지)
       - connection.type == tool.type 정합성 (불일치 422)
    5) 반영 후 commit + connection eager refresh
    """
    result = await db.execute(
        select(Tool)
        .where(Tool.id == tool_id)
        .options(selectinload(Tool.connection))
    )
    tool = result.scalar_one_or_none()
    if tool is None:
        raise tool_not_found()

    # 정보 노출 방지: 타 유저 소유 user-tool은 404로 위장.
    # is_system=True PREBUILT 행은 글로벌 자산이므로 ownership 체크에서 제외 —
    # 아래 PREBUILT 분기에서 400으로 거부된다.
    if not tool.is_system and tool.user_id != user_id:
        raise tool_not_found()

    if tool.type == ToolType.PREBUILT:
        raise HTTPException(
            status_code=400,
            detail=(
                "PREBUILT tools use (user_id, provider_name) scoped connections; "
                "PATCH /api/tools/{id} does not apply. Manage the connection in "
                "/connections instead."
            ),
        )

    if payload.connection_id is not None:
        conn_result = await db.execute(
            select(Connection)
            .where(Connection.id == payload.connection_id)
            .options(selectinload(Connection.credential))
        )
        connection = conn_result.scalar_one_or_none()
        if connection is None or connection.user_id != user_id:
            # IDOR 방지: 다른 유저의 connection 존재 자체를 노출하지 않는다
            raise HTTPException(status_code=404, detail="Connection not found")
        if connection.type != tool.type:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Connection type '{connection.type}' does not match "
                    f"tool type '{tool.type}'"
                ),
            )

    tool.connection_id = payload.connection_id
    await db.commit()
    await db.refresh(tool, attribute_names=["connection"])
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
