"""Tool domain service (BE-S2) + assistant/builder catalog shim.

Owns Tool CRUD queries, mutations and side effects (audit records) for
:mod:`app.routers.tools`; routers keep HTTP concerns only (schema conversion,
``Depends`` guards, commits). Also keeps the read-only catalog view the
Builder/Assistant sub-agents consume (``get_tools_catalog``).

Transaction policy: the service ``flush``es, the calling router ``commit``s.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.validation import require_user_credential
from app.dependencies import CurrentUser
from app.error_codes import tool_not_found
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool
from app.schemas.tool import ToolCreate, ToolPatch
from app.services import audit_service
from app.tools.registry import registry as tool_registry
from app.tools.runner import ToolRunResult, run_tool

# -- Queries -------------------------------------------------------------------


async def load_owned(db: AsyncSession, tool_id: uuid.UUID, user_id: uuid.UUID) -> Tool:
    row = (
        await db.execute(
            select(Tool).where(
                Tool.id == tool_id,
                # Either owned by the current user or a system-owned (NULL) tool.
                Tool.visible_to(user_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise tool_not_found()
    return row


async def list_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    definition_key: str | None = None,
    enabled: bool | None = None,
) -> list[Tool]:
    stmt = select(Tool).where(Tool.visible_to(user_id))
    if definition_key is not None:
        stmt = stmt.where(Tool.definition_key == definition_key)
    if enabled is not None:
        stmt = stmt.where(Tool.enabled == enabled)
    rows = (await db.execute(stmt.order_by(Tool.created_at.desc()))).scalars().all()
    return list(rows)


# -- Mutations -------------------------------------------------------------------


async def create_tool(db: AsyncSession, *, user_id: uuid.UUID, data: ToolCreate) -> Tool:
    if tool_registry.get(data.definition_key) is None:
        raise HTTPException(status_code=400, detail=f"unknown definition '{data.definition_key}'")
    await require_user_credential(db, credential_id=data.credential_id, user_id=user_id)

    tool = Tool(
        user_id=user_id,
        definition_key=data.definition_key,
        name=data.name,
        description=data.description,
        parameters=data.parameters,
        credential_id=data.credential_id,
        enabled=data.enabled,
    )
    db.add(tool)
    await db.flush()
    return tool


async def update_tool(
    db: AsyncSession,
    *,
    tool: Tool,
    user_id: uuid.UUID,
    data: ToolPatch,
) -> Tool:
    if data.name is not None:
        tool.name = data.name
    if data.description is not None:
        tool.description = data.description
    if data.parameters is not None:
        tool.parameters = data.parameters
    if data.credential_id is not None or "credential_id" in data.model_fields_set:
        await require_user_credential(db, credential_id=data.credential_id, user_id=user_id)
        tool.credential_id = data.credential_id
    if data.enabled is not None:
        tool.enabled = data.enabled
    await db.flush()
    return tool


async def delete_tool(db: AsyncSession, *, tool: Tool) -> None:
    await db.delete(tool)
    await db.flush()


async def run_tool_instance(
    db: AsyncSession,
    *,
    tool: Tool,
    runtime_args: dict[str, Any] | None,
) -> ToolRunResult:
    """Execute the tool once and stamp ``last_used_at`` on success."""

    result = await run_tool(
        db=db,
        tool=tool,
        registry=tool_registry,
        runtime_args=runtime_args,
    )
    if result.success:
        tool.last_used_at = datetime.now(UTC).replace(tzinfo=None)
    return result


# -- Side effects ----------------------------------------------------------------


async def record_tool_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    tool: Tool,
    outcome: str = "success",
    reason_code: str | None = None,
    reason_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="tool",
        target_id=tool.id,
        target_name_snapshot=tool.name,
        target_owner_user_id=user.id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        request=request,
        metadata={"definition_key": tool.definition_key, **(metadata or {})},
    )


# -- Assistant/builder catalog ---------------------------------------------------


async def get_tools_catalog(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return the user's available items: ``Tool`` + ``McpTool`` + ``Skill``.

    Each item carries a ``kind`` field (``"tool" | "mcp" | "skill"``) so the
    builder phase3 추천기 LLM 이 상황에 맞는 종류를 선택할 수 있고, phase8
    confirm 이 종류별로 적절한 link 테이블에 매칭한다.
    """

    tool_rows = await db.execute(
        select(Tool.id, Tool.name, Tool.description, Tool.definition_key).where(
            Tool.visible_to(user_id)
        )
    )
    items: list[dict[str, Any]] = [
        {
            "id": str(row.id),
            "kind": "tool",
            "name": row.name,
            "description": row.description or "",
            "definition_key": row.definition_key,
        }
        for row in tool_rows.all()
    ]

    mcp_rows = await db.execute(
        select(
            McpTool.id,
            McpTool.name,
            McpTool.description,
            McpTool.enabled,
            McpServer.id.label("server_id"),
            McpServer.name.label("server_name"),
        )
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(McpServer.user_id == user_id)
    )
    items.extend(
        {
            "id": str(row.id),
            "kind": "mcp",
            "name": row.name,
            "description": row.description or "",
            "server_id": str(row.server_id),
            "server_name": row.server_name,
            "enabled": bool(row.enabled),
        }
        for row in mcp_rows.all()
    )

    skill_rows = await db.execute(
        select(Skill.id, Skill.name, Skill.description, Skill.slug).where(Skill.user_id == user_id)
    )
    items.extend(
        {
            "id": str(row.id),
            "kind": "skill",
            "name": row.name,
            "description": row.description or "",
            "slug": row.slug,
        }
        for row in skill_rows.all()
    )

    return items
