"""Trusted persistence and authorization store for MCP App invocation bindings."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.mcp_app_scope import (
    McpAppBindingTarget,
    McpAppRunTarget,
    linked_tool,
    load_private_mcp_app_run,
    private_mcp_app_binding_query,
)
from app.authz.org_context import OrgContext
from app.authz.scoping import resource_candidates
from app.database import async_session, tenant_session
from app.mcp.apps import (
    is_ui_resource_uri,
    normalize_tool_ui_meta,
    sanitize_result_meta,
    sanitize_result_payload,
    tool_allows_visibility,
)
from app.mcp.apps_binding_lifecycle import McpAppsError, require_live_agent_tree
from app.mcp.domain import McpAppRuntimeContext
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool


@dataclass(frozen=True, slots=True)
class BindingDraft:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    org_context: OrgContext
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
    org_context: OrgContext


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
    run = await load_private_mcp_app_run(
        db, McpAppRunTarget(draft.conversation_id, draft.run_id), draft.org_context
    )
    if (
        run is None
        or run.agent_id is None
        or run.agent_id != draft.agent_id
        or (
            draft.user_id != draft.org_context.user_id
            or draft.credential_subject_user_id != draft.org_context.user_id
        )
    ):
        raise McpAppsError("mcp_app_run_not_found", "run context is not available", 404)
    await require_live_agent_tree(db, run.agent_id, draft.org_context)
    tool = await linked_tool(
        db,
        agent_id=draft.agent_id,
        server_id=draft.mcp_server_id,
        tool_id=draft.mcp_tool_id,
        tool_name=draft.remote_tool_name,
        context=draft.org_context,
    )
    if tool is None:
        raise McpAppsError("mcp_app_tool_revoked", "MCP tool binding is not available", 403)

    existing = await db.scalar(
        select(McpAppInvocationBinding)
        .where(
            McpAppInvocationBinding.run_id == draft.run_id,
            McpAppInvocationBinding.tool_call_id == draft.tool_call_id,
        )
        .execution_options(autoflush=False, populate_existing=True)
    )
    if existing is not None:
        if (
            existing.mcp_tool_id != draft.mcp_tool_id
            or existing.resource_uri != draft.resource_uri
            or existing.conversation_id != draft.conversation_id
            or existing.agent_id != draft.agent_id
            or existing.user_id != draft.user_id
            or existing.credential_subject_user_id != draft.credential_subject_user_id
            or existing.mcp_server_id != draft.mcp_server_id
        ):
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
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> str | None:
    """Mint trusted provenance from the post-invocation runtime callback."""

    try:
        parsed_run_id = uuid.UUID(run_id)
    except ValueError:
        return None
    async with tenant_session(context.org_context, maker=session_factory or async_session) as db:
        binding_id = await mint_invocation_binding(
            db,
            BindingDraft(
                run_id=parsed_run_id,
                conversation_id=context.conversation_id,
                user_id=context.user_id,
                org_context=context.org_context,
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
    context: OrgContext,
) -> AuthorizedBinding:
    binding = await db.scalar(
        private_mcp_app_binding_query(
            McpAppBindingTarget(conversation_id, run_id, tool_call_id), context
        ).execution_options(autoflush=False, populate_existing=True)
    )
    run = await load_private_mcp_app_run(db, McpAppRunTarget(conversation_id, run_id), context)
    if (
        binding is None
        or run is None
        or run.agent_id is None
        or binding.agent_id != run.agent_id
        or (binding.credential_subject_user_id != context.user_id)
    ):
        raise McpAppsError("mcp_app_not_found", "MCP App context was not found", 404)
    await require_live_agent_tree(db, run.agent_id, context)
    tool = await linked_tool(
        db,
        agent_id=run.agent_id,
        server_id=binding.mcp_server_id,
        tool_id=binding.mcp_tool_id,
        tool_name=binding.remote_tool_name,
        context=context,
    )
    server = await db.scalar(
        resource_candidates(
            select(McpServer).where(McpServer.id == binding.mcp_server_id),
            McpServer,
            context,
        ).execution_options(autoflush=False, populate_existing=True)
    )
    current_meta = normalize_tool_ui_meta(tool.metadata_json) if tool is not None else None
    if tool is None or server is None or server.status == "disabled":
        raise McpAppsError("mcp_app_binding_revoked", "MCP App binding is no longer active", 403)
    if current_meta is None or current_meta["ui"].get("resourceUri") != binding.resource_uri:
        raise McpAppsError("mcp_app_context_stale", "MCP App context is stale", 409)
    return AuthorizedBinding(
        binding=binding, run=run, server=server, origin_tool=tool, org_context=context
    )


__all__ = [
    "AuthorizedBinding",
    "BindingDraft",
    "McpAppsError",
    "authorize_binding",
    "linked_tool",
    "mint_invocation_binding",
    "record_runtime_binding",
    "require_live_agent_tree",
]
