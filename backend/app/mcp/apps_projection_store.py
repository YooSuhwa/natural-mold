"""Database-backed canonical MCP App artifact projection store."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.schemas.mcp_apps import McpAppArtifact


async def load_verified_artifact(
    *,
    conversation_id: str,
    run_id: str,
    tool_call_id: str,
    binding_id: str,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = async_session,
) -> dict[str, Any] | None:
    """Load canonical data only when run, tool call, and binding all match."""

    try:
        parsed_conversation_id = uuid.UUID(conversation_id)
        parsed_run_id = uuid.UUID(run_id)
        parsed_binding_id = uuid.UUID(binding_id)
    except ValueError:
        return None
    async with session_factory() as db:
        row = await db.scalar(
            select(McpAppInvocationBinding).where(
                McpAppInvocationBinding.id == parsed_binding_id,
                McpAppInvocationBinding.conversation_id == parsed_conversation_id,
                McpAppInvocationBinding.run_id == parsed_run_id,
                McpAppInvocationBinding.tool_call_id == tool_call_id,
            )
        )
    if row is None:
        return None
    app = McpAppArtifact(
        binding_id=row.id,
        run_id=row.run_id,
        resource_uri=row.resource_uri,
        tool_meta=row.tool_meta,
        result_meta=row.result_meta,
    ).model_dump(mode="json", exclude_none=True)
    artifact: dict[str, Any] = {"mcp_app": app}
    if row.structured_content is not None:
        artifact["structured_content"] = row.structured_content
    return artifact


__all__ = ["load_verified_artifact"]
