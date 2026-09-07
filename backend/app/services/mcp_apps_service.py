"""Trusted MCP Apps invocation provenance and scoped proxy operations."""

from __future__ import annotations

import base64
import json
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.run_secrets import collect_secret_values
from app.mcp.apps import (
    normalize_resource_ui_meta,
    normalize_tool_ui_meta,
    sanitize_result_meta,
    sanitize_result_payload,
    tool_allows_visibility,
)
from app.mcp.auth import ResolvedMcpAuth, resolve_mcp_auth
from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once
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


async def read_bound_resource(db: AsyncSession, context: AuthorizedBinding) -> dict[str, Any]:
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
    if len(json.dumps(arguments, separators=(",", ":")).encode()) > MAX_TOOL_ARGUMENT_BYTES:
        raise McpAppsError("mcp_app_arguments_too_large", "tool arguments are too large", 413)
    tool = await linked_tool(
        db,
        agent_id=context.run.agent_id,
        server_id=context.binding.mcp_server_id,
        tool_id=None,
        tool_name=tool_name,
        owner_user_id=context.binding.credential_subject_user_id,
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
    auth = await resolve_mcp_auth(
        db,
        credential_id=context.server.credential_id,
        user_id=context.binding.credential_subject_user_id,
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
    "record_runtime_binding",
]
