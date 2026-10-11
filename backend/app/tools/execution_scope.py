"""Explicit private execution scope for standalone registered tools."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import httpx
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import resource_candidates
from app.error_codes import tool_not_found
from app.models.tool import Tool
from app.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolRunTarget:
    tool_id: UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class ToolRunOptions:
    registry: ToolRegistry
    runtime_args: Mapping[str, JsonValue] | None = None
    http_client: httpx.AsyncClient | None = None


async def load_execution_tool(db: AsyncSession, target: ToolRunTarget) -> Tool:
    context = target.context
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    tool = await db.scalar(
        resource_candidates(select(Tool), Tool, context)
        .where(Tool.id == target.tool_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tool is None:
        raise tool_not_found()
    return tool
