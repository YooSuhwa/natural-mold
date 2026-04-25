from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.error_codes import tool_not_found
from app.models.connection import Connection
from app.models.tool import AgentToolLink, Tool
from app.schemas.tool import ToolCustomCreate, ToolResponse, ToolType, ToolUpdate
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

    # 빈 body(`{}`)로 들어오면 connection_id 필드가 "미전송" 상태 → 기존 바인딩
    # 유지. 명시적 해제는 `{"connection_id": null}` 전송으로만 가능.
    patch_fields = payload.model_dump(exclude_unset=True)
    if "connection_id" not in patch_fields:
        return tool

    new_connection_id = patch_fields["connection_id"]
    if new_connection_id is not None:
        conn_result = await db.execute(
            select(Connection)
            .where(Connection.id == new_connection_id)
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
        # 런타임 invariant 정합 — chat_service._gate_connection_active /
        # _gate_connection_credential과 같은 조건. PATCH 시점에 거부해 dead-on-
        # arrival tool을 막는다. create_custom_tool도 같은 검증을 수행함.
        if connection.status != "active":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Connection {connection.id} is status='{connection.status}'. "
                    "Reactivate the connection before binding."
                ),
            )
        if tool.type == ToolType.CUSTOM and connection.credential_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"CUSTOM tools require a connection with a credential "
                    f"attached. Connection {connection.id} has no credential."
                ),
            )

    tool.connection_id = new_connection_id
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


async def discover_mcp_tools(
    db: AsyncSession,
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """POST /api/connections/{id}/discover-tools — MCP 서버에서 tool 목록 발견 후 upsert.

    m9 마이그레이션 경로의 사용자 대체물: MCP connection이 이미 `/api/connections`로
    생성되면, 이 엔드포인트로 실제 서버에서 tool 스키마를 읽어 Tool 레코드로 승격한다.

    검증 순서:
    1) connection 존재 + 소유 확인 (IDOR 방지: 타 유저는 404)
    2) connection.type == 'mcp' 아니면 422
    3) extra_config.url 없으면 422
    4) `test_mcp_connection` (JSON-RPC probe) 호출 — success=False면 502
    5) 반환된 tools를 user_id×connection_id×name 기준으로 upsert:
       - name 이미 있으면 skip (status=existing)
       - 없으면 신규 Tool(type='mcp') 생성 (status=created)

    응답: {connection_id, server_info, items: [{tool, status}]}
    """
    from app.agent_runtime.mcp_client import test_mcp_connection as mcp_probe
    from app.services.env_var_resolver import resolve_env_vars

    result = await db.execute(
        select(Connection)
        .where(Connection.id == connection_id)
        .options(selectinload(Connection.credential))
    )
    conn = result.scalar_one_or_none()
    if conn is None or conn.user_id != user_id:
        # IDOR 방지: 타 유저 connection의 존재 자체를 노출하지 않는다
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.type != "mcp":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Connection type '{conn.type}' does not support tool discovery; "
                "only 'mcp' connections expose a remote tool catalog."
            ),
        )
    # kill-switch: 사용자가 disable한 connection은 원격 probe/생성을 막는다.
    # chat_service._gate_connection_active와 정책 정합 (disabled = 실행 불가).
    if conn.status != "active":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Connection {conn.id} is status='{conn.status}' — "
                "reactivate before discovering tools."
            ),
        )

    extra = conn.extra_config or {}
    url = extra.get("url")
    if not url:
        raise HTTPException(
            status_code=422,
            detail=f"Connection {conn.id} has no extra_config.url",
        )

    effective_auth = resolve_env_vars(
        extra.get("env_vars"),
        conn.credential,
        context={"connection_id": str(conn.id)},
    )
    # transport headers (인증/테넌트 헤더 등)는 chat runtime의 MCP 빌더와 동일하게
    # probe에도 전달해야 동일 카탈로그가 나온다 — 누락 시 인증 MCP에서 401/잘못된 목록.
    extra_headers = extra.get("headers")
    if not isinstance(extra_headers, dict):
        extra_headers = None
    probe = await mcp_probe(url, effective_auth, extra_headers=extra_headers)
    if not probe.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"MCP discovery failed: {probe.get('error') or 'unknown error'}",
        )

    discovered = probe.get("tools") or []
    server_info = probe.get("server_info") or {}

    existing_result = await db.execute(
        select(Tool).where(
            Tool.user_id == user_id,
            Tool.type == ToolType.MCP,
            Tool.connection_id == connection_id,
        )
    )
    existing_by_name = {t.name: t for t in existing_result.scalars().all()}

    # Tool.name은 String(100) — 원격 서버가 보낸 oversized name을 그대로 insert하면
    # DataError로 500 응답. 검증 단계에서 skip.
    MAX_TOOL_NAME_LEN = 100

    created: list[Tool] = []
    skipped: list[Tool] = []
    invalid_count = 0
    for entry in discovered:
        if not isinstance(entry, dict):
            invalid_count += 1
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_LEN:
            invalid_count += 1
            continue
        if name in existing_by_name:
            skipped.append(existing_by_name[name])
            continue
        new_tool = Tool(
            user_id=user_id,
            type=ToolType.MCP,
            name=name,
            description=(entry.get("description") or "")[:10000] or None,
            parameters_schema=entry.get("inputSchema") or entry.get("input_schema"),
            connection_id=connection_id,
        )
        # m14 partial unique index가 (user_id, connection_id, name) WHERE type='mcp'
        # 중복을 거부한다. 각 insert를 SAVEPOINT(begin_nested)로 격리 — IntegrityError
        # 발생 시 savepoint만 rollback되고 이전 created 행들은 그대로 보존된다.
        # session-wide `db.rollback()` 사용 금지: 동시성 race가 한 번 잡혀도 같은
        # discovery 호출의 이전 inserts가 함께 사라지는 silent catalog drift 발생.
        try:
            async with db.begin_nested():
                db.add(new_tool)
                await db.flush()
        except IntegrityError:
            # savepoint만 rollback. 외부 트랜잭션의 다른 created rows는 살아있음.
            refetch = await db.execute(
                select(Tool).where(
                    Tool.user_id == user_id,
                    Tool.type == ToolType.MCP,
                    Tool.connection_id == connection_id,
                    Tool.name == name,
                )
            )
            winner = refetch.scalar_one_or_none()
            if winner is not None:
                skipped.append(winner)
                existing_by_name[name] = winner
            continue
        created.append(new_tool)

    if created or skipped:
        # 잔여 outer transaction commit. savepoint는 row 단위지만 commit은 한 번.
        await db.commit()

    items: list[dict[str, Any]] = []
    for t in created:
        items.append({"tool": ToolResponse.model_validate(t), "status": "created"})
    for t in skipped:
        items.append({"tool": ToolResponse.model_validate(t), "status": "existing"})

    return {
        "connection_id": connection_id,
        "server_info": server_info,
        "items": items,
    }
