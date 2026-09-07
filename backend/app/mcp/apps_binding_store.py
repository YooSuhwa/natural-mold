"""Trusted persistence and authorization store for MCP App invocation bindings."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.mcp.apps import (
    is_ui_resource_uri,
    normalize_tool_ui_meta,
    sanitize_result_meta,
    sanitize_result_payload,
    tool_allows_visibility,
)
from app.mcp.domain import McpAppRuntimeContext
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool


@dataclass(frozen=True, slots=True)
class BindingDraft:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    credential_subject_user_id: uuid.UUID
    mcp_server_id: uuid.UUID
    mcp_tool_id: uuid.UUID
    tool_call_id: str
    remote_tool_name: str
    resource_uri: str
    tool_meta: dict[str, Any]
    result_meta: dict[str, Any] | None
    structured_content: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AuthorizedBinding:
    binding: McpAppInvocationBinding
    run: ConversationRun
    server: McpServer
    origin_tool: McpTool


@dataclass(frozen=True, slots=True)
class McpAppsError(Exception):
    code: str
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


async def linked_tool(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    server_id: uuid.UUID,
    tool_id: uuid.UUID | None,
    tool_name: str,
    owner_user_id: uuid.UUID,
) -> McpTool | None:
    stmt = (
        select(McpTool)
        .join(AgentMcpToolLink, AgentMcpToolLink.mcp_tool_id == McpTool.id)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(
            AgentMcpToolLink.agent_id == agent_id,
            McpTool.server_id == server_id,
            McpTool.name == tool_name,
            McpTool.enabled.is_(True),
            McpServer.user_id == owner_user_id,
            McpServer.status != "disabled",
        )
    )
    if tool_id is not None:
        stmt = stmt.where(McpTool.id == tool_id)
    return await db.scalar(stmt)


async def mint_invocation_binding(db: AsyncSession, draft: BindingDraft) -> uuid.UUID:
    """Persist provenance only after a runtime MCP call returned successfully."""

    tool_meta = normalize_tool_ui_meta(draft.tool_meta)
    if (
        tool_meta is None
        or not is_ui_resource_uri(draft.resource_uri)
        or tool_meta["ui"].get("resourceUri") != draft.resource_uri
        or not tool_allows_visibility(tool_meta, "model")
    ):
        raise McpAppsError("mcp_app_metadata_invalid", "invalid MCP App metadata", 422)
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == draft.run_id,
            ConversationRun.conversation_id == draft.conversation_id,
            ConversationRun.user_id == draft.user_id,
            ConversationRun.agent_id == draft.agent_id,
        )
    )
    if run is None:
        raise McpAppsError("mcp_app_run_not_found", "run context is not available", 404)
    tool = await linked_tool(
        db,
        agent_id=draft.agent_id,
        server_id=draft.mcp_server_id,
        tool_id=draft.mcp_tool_id,
        tool_name=draft.remote_tool_name,
        owner_user_id=draft.credential_subject_user_id,
    )
    if tool is None:
        raise McpAppsError("mcp_app_tool_revoked", "MCP tool binding is not available", 403)

    existing = await db.scalar(
        select(McpAppInvocationBinding).where(
            McpAppInvocationBinding.run_id == draft.run_id,
            McpAppInvocationBinding.tool_call_id == draft.tool_call_id,
        )
    )
    if existing is not None:
        if existing.mcp_tool_id != draft.mcp_tool_id or existing.resource_uri != draft.resource_uri:
            raise McpAppsError("mcp_app_tool_call_conflict", "tool call provenance conflicts", 409)
        return existing.id

    structured_content = sanitize_result_payload(draft.structured_content)
    if draft.structured_content is not None and not isinstance(structured_content, dict):
        raise McpAppsError("mcp_app_result_too_large", "tool result is too large", 413)
    row = McpAppInvocationBinding(
        run_id=draft.run_id,
        conversation_id=draft.conversation_id,
        user_id=draft.user_id,
        agent_id=draft.agent_id,
        credential_subject_user_id=draft.credential_subject_user_id,
        mcp_server_id=draft.mcp_server_id,
        mcp_tool_id=draft.mcp_tool_id,
        tool_call_id=draft.tool_call_id,
        remote_tool_name=draft.remote_tool_name,
        resource_uri=draft.resource_uri,
        tool_meta=tool_meta,
        result_meta=sanitize_result_meta(draft.result_meta),
        structured_content=structured_content,
    )
    db.add(row)
    await db.flush()
    return row.id


async def record_runtime_binding(
    context: McpAppRuntimeContext,
    run_id: str,
    tool_call_id: str,
    result_meta: dict[str, Any] | None,
    structured_content: dict[str, Any] | None,
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = async_session,
) -> str | None:
    """Mint trusted provenance from the post-invocation runtime callback."""

    try:
        parsed_run_id = uuid.UUID(run_id)
    except ValueError:
        return None
    async with session_factory() as db:
        binding_id = await mint_invocation_binding(
            db,
            BindingDraft(
                run_id=parsed_run_id,
                conversation_id=context.conversation_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                credential_subject_user_id=context.credential_subject_user_id,
                mcp_server_id=context.mcp_server_id,
                mcp_tool_id=context.mcp_tool_id,
                tool_call_id=tool_call_id,
                remote_tool_name=context.remote_tool_name,
                resource_uri=context.resource_uri,
                tool_meta=context.tool_meta,
                result_meta=result_meta,
                structured_content=structured_content,
            ),
        )
        await db.commit()
        return str(binding_id)


async def authorize_binding(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    user_id: uuid.UUID,
) -> AuthorizedBinding:
    binding = await db.scalar(
        select(McpAppInvocationBinding).where(
            McpAppInvocationBinding.conversation_id == conversation_id,
            McpAppInvocationBinding.run_id == run_id,
            McpAppInvocationBinding.tool_call_id == tool_call_id,
            McpAppInvocationBinding.user_id == user_id,
        )
    )
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.user_id == user_id,
        )
    )
    if binding is None or run is None or binding.agent_id != run.agent_id:
        raise McpAppsError("mcp_app_not_found", "MCP App context was not found", 404)
    tool = await linked_tool(
        db,
        agent_id=run.agent_id,
        server_id=binding.mcp_server_id,
        tool_id=binding.mcp_tool_id,
        tool_name=binding.remote_tool_name,
        owner_user_id=binding.credential_subject_user_id,
    )
    server = await db.scalar(
        select(McpServer).where(
            McpServer.id == binding.mcp_server_id,
            McpServer.user_id == binding.credential_subject_user_id,
        )
    )
    current_meta = normalize_tool_ui_meta(tool.metadata_json) if tool is not None else None
    if tool is None or server is None or server.status == "disabled":
        raise McpAppsError("mcp_app_binding_revoked", "MCP App binding is no longer active", 403)
    if current_meta is None or current_meta["ui"].get("resourceUri") != binding.resource_uri:
        raise McpAppsError("mcp_app_context_stale", "MCP App context is stale", 409)
    return AuthorizedBinding(binding=binding, run=run, server=server, origin_tool=tool)


__all__ = [
    "AuthorizedBinding",
    "BindingDraft",
    "McpAppsError",
    "authorize_binding",
    "linked_tool",
    "mint_invocation_binding",
    "record_runtime_binding",
]
