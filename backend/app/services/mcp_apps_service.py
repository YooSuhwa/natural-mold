"""Trusted MCP Apps invocation provenance and scoped proxy operations."""

from __future__ import annotations

import base64
import json
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.run_secrets import collect_secret_values, collect_url_userinfo
from app.credentials.run_resolution import USABLE_STATUSES
from app.mcp.apps import (
    normalize_resource_ui_meta,
    normalize_tool_ui_meta,
    sanitize_result_meta,
    sanitize_result_payload,
    tool_allows_visibility,
)
from app.mcp.apps_binding_connection import credential_for_context
from app.mcp.apps_binding_store import require_live_agent_tree
from app.mcp.auth import ResolvedMcpAuth, resolve_mcp_auth
from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once
from app.schemas.mcp_apps import McpAppArtifact, McpAppWidgetResult
from app.services.mcp_apps_binding_service import (
    AuthorizedBinding,
    BindingDraft,
    McpAppsError,
    authorize_binding,
    linked_tool,
    mint_invocation_binding,
    record_runtime_binding,
)

MCP_APP_MIME_TYPE: Final = "text/html;profile=mcp-app"
MAX_APP_HTML_BYTES: Final = 640 * 1024
MAX_TOOL_ARGUMENT_BYTES: Final = 64 * 1024


async def read_widget_result(db: AsyncSession, context: AuthorizedBinding) -> McpAppWidgetResult:
    """Restore a verified invocation's stored result without opening or refreshing credentials."""
    credential = await credential_for_context(db, context)
    if credential is not None and credential.status not in USABLE_STATUSES:
        raise McpAppsError("mcp_app_auth_unavailable", "MCP authentication is unavailable", 403)
    binding = context.binding
    tool_meta = normalize_tool_ui_meta(context.origin_tool.metadata_json)
    secrets = collect_secret_values(context.server.headers)
    collect_url_userinfo(context.server.url, secrets)
    # The runtime masked resolved credentials before minting the binding. Reapply
    # bounds and masking at read time to cover old rows and current transport headers.
    if (
        tool_meta is None
        or tool_meta["ui"].get("resourceUri") != binding.resource_uri
        or redact_protocol_data("resources/read", binding.resource_uri, secret_values=secrets)
        != binding.resource_uri
    ):
        raise McpAppsError("mcp_app_context_stale", "MCP App context is stale", 409)
    content = sanitize_result_payload(binding.structured_content)
    if binding.structured_content is not None and not isinstance(content, dict):
        raise McpAppsError("mcp_app_result_invalid", "MCP App result is invalid", 409)
    return McpAppWidgetResult(
        mcp_app=McpAppArtifact(
            binding_id=binding.id,
            run_id=binding.run_id,
            resource_uri=binding.resource_uri,
            tool_meta=tool_meta,
            result_meta=redact_protocol_data(
                "tools/call", sanitize_result_meta(binding.result_meta), secret_values=secrets
            ),
        ),
        structured_content=redact_protocol_data("tools/call", content, secret_values=secrets),
    )


async def read_bound_resource(db: AsyncSession, context: AuthorizedBinding) -> dict[str, Any]:
    await require_live_agent_tree(db, context.run.agent_id, context.org_context)
    auth = await _auth_for_context(db, context)
    raw = await read_mcp_resource_once(
        transport=context.server.transport,
        url=context.server.url,
        headers=auth.headers,
        resource_uri=context.binding.resource_uri,
    )
    if not raw.get("success"):
        raise McpAppsError("mcp_app_resource_failed", "MCP App resource read failed", 502)
    matches = [
        item
        for item in raw.get("contents") or []
        if isinstance(item, dict) and item.get("uri") == context.binding.resource_uri
    ]
    if len(matches) != 1 or matches[0].get("mimeType") != MCP_APP_MIME_TYPE:
        raise McpAppsError("mcp_app_resource_invalid", "invalid MCP App resource response", 502)
    item = matches[0]
    html = _resource_html(item)
    secrets = collect_secret_values(auth.credentials) | collect_secret_values(auth.headers)
    redacted_html = redact_protocol_data("resources/read", html, secret_values=secrets)
    if redacted_html != html:
        raise McpAppsError(
            "mcp_app_resource_sensitive", "MCP App resource contains sensitive data", 502
        )
    meta = normalize_resource_ui_meta(item.get("_meta") or item.get("meta"))
    return {
        "uri": context.binding.resource_uri,
        "mimeType": MCP_APP_MIME_TYPE,
        "html": html,
        "meta": meta["ui"] if meta is not None else {},
    }


async def list_bound_resources(context: AuthorizedBinding) -> dict[str, Any]:
    return {
        "resources": [
            {
                "uri": context.binding.resource_uri,
                "name": context.origin_tool.name,
                "mimeType": MCP_APP_MIME_TYPE,
                "_meta": context.binding.tool_meta,
            }
        ]
    }


async def call_bound_tool(
    db: AsyncSession,
    context: AuthorizedBinding,
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    agent_id = await require_live_agent_tree(db, context.run.agent_id, context.org_context)
    if len(json.dumps(arguments, separators=(",", ":")).encode()) > MAX_TOOL_ARGUMENT_BYTES:
        raise McpAppsError("mcp_app_arguments_too_large", "tool arguments are too large", 413)
    tool = await linked_tool(
        db,
        agent_id=agent_id,
        server_id=context.binding.mcp_server_id,
        tool_id=None,
        tool_name=tool_name,
        context=context.org_context,
    )
    meta = normalize_tool_ui_meta(tool.metadata_json) if tool is not None else None
    if tool is None or not tool_allows_visibility(meta, "app"):
        raise McpAppsError("mcp_app_tool_forbidden", "app tool is not linked or callable", 403)
    auth = await _auth_for_context(db, context)
    raw = await call_mcp_tool_once(
        transport=context.server.transport,
        url=context.server.url,
        headers=auth.headers,
        tool_name=tool.name,
        arguments=arguments,
    )
    if not raw.get("success"):
        raise McpAppsError("mcp_app_tool_failed", "MCP App tool call failed", 502)
    content = sanitize_result_payload(raw.get("content"))
    structured = sanitize_result_payload(raw.get("structured_content"))
    if content is None and raw.get("content") is not None:
        raise McpAppsError("mcp_app_result_too_large", "tool result is too large", 502)
    if structured is None and raw.get("structured_content") is not None:
        raise McpAppsError("mcp_app_result_too_large", "tool result is too large", 502)
    secrets = collect_secret_values(auth.credentials) | collect_secret_values(auth.headers)
    result = {
        "content": content or [],
        "structuredContent": structured,
        "_meta": sanitize_result_meta(raw.get("meta")) or {},
    }
    redacted = redact_protocol_data("tools/call", result, secret_values=secrets)
    return redacted if isinstance(redacted, dict) else {}


async def _auth_for_context(db: AsyncSession, context: AuthorizedBinding) -> ResolvedMcpAuth:
    credential = await credential_for_context(db, context)
    auth = await resolve_mcp_auth(
        db,
        credential_id=credential.id if credential else None,
        user_id=credential.user_id if credential else None,
        static_headers=context.server.headers,
    )
    if auth.error:
        raise McpAppsError("mcp_app_auth_unavailable", "MCP authentication is unavailable", 403)
    return auth


def _resource_html(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        raw = text.encode()
    elif isinstance(item.get("blob"), str):
        try:
            raw = base64.b64decode(item["blob"], validate=True)
        except (ValueError, TypeError) as exc:
            raise McpAppsError("mcp_app_resource_invalid", "invalid base64 resource", 502) from exc
    else:
        raise McpAppsError("mcp_app_resource_invalid", "resource HTML is missing", 502)
    if len(raw) > MAX_APP_HTML_BYTES:
        raise McpAppsError("mcp_app_resource_too_large", "resource HTML is too large", 413)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McpAppsError("mcp_app_resource_invalid", "resource HTML is not UTF-8", 502) from exc


__all__ = [
    "AuthorizedBinding",
    "BindingDraft",
    "McpAppsError",
    "authorize_binding",
    "call_bound_tool",
    "list_bound_resources",
    "mint_invocation_binding",
    "read_bound_resource",
    "read_widget_result",
    "record_runtime_binding",
]
