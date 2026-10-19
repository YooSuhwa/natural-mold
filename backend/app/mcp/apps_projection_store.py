"""Database-backed canonical MCP App artifact projection store."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionTenant, async_session, platform_session, tenant_session
from app.models.conversation import Conversation
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.schemas.mcp_apps import McpAppArtifact

OwnerCache = dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID] | None]


async def _conversation_owner(
    conversation_id: uuid.UUID,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None,
    owner_cache: OwnerCache | None,
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """I-06: the held conversation's company and owner (who minted its bindings).

    Looked up once in platform scope; the binding is then read in that company.
    """
    if owner_cache is not None and conversation_id in owner_cache:
        return owner_cache[conversation_id]
    async with platform_session(
        "mcp_apps.conversation_company", maker=session_factory or async_session
    ) as db:
        row = (
            await db.execute(
                select(Conversation.tenant_id, Conversation.user_id).where(
                    Conversation.id == conversation_id
                )
            )
        ).one_or_none()
    owner = None if row is None else (row[0], row[1])
    if owner_cache is not None:
        owner_cache[conversation_id] = owner
    return owner


async def load_verified_artifact(
    *,
    conversation_id: str,
    run_id: str,
    tool_call_id: str,
    binding_id: str,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    owner_cache: OwnerCache | None = None,
) -> dict[str, Any] | None:
    """Load canonical data only when run, tool call, and binding all match.

    ``owner_cache`` (one dict per projection pass) keeps the conversation's company
    lookup to once per conversation instead of once per candidate.
    """

    try:
        parsed_conversation_id = uuid.UUID(conversation_id)
        parsed_run_id = uuid.UUID(run_id)
        parsed_binding_id = uuid.UUID(binding_id)
    except ValueError:
        return None
    owner = await _conversation_owner(parsed_conversation_id, session_factory, owner_cache)
    if owner is None:
        return None
    async with tenant_session(
        SessionTenant(owner[0], owner[1]), maker=session_factory or async_session
    ) as db:
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


__all__ = ["OwnerCache", "load_verified_artifact"]
