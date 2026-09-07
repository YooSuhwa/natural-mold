"""MCP Apps metadata/result bridging for runtime MCP tools."""

from __future__ import annotations

import uuid
from typing import Any

from app.mcp.apps_binding_store import record_runtime_binding
from app.mcp.domain import McpAppRuntimeContext


class ResultMetaInterceptor:
    """Carry result metadata through the adapter's structured artifact seam."""

    async def __call__(self, request: Any, handler: Any) -> Any:
        result = await handler(request)
        result_meta = getattr(result, "meta", None)
        if result_meta is None or not hasattr(result, "model_copy"):
            return result
        from app.agent_runtime.mcp_cache import _MCP_RESULT_ENVELOPE

        return result.model_copy(
            update={
                "structuredContent": {
                    _MCP_RESULT_ENVELOPE: {
                        "structured_content": getattr(result, "structuredContent", None),
                        "result_meta": result_meta,
                    }
                }
            }
        )


def mcp_app_context(
    config: dict[str, Any] | None,
    tool_meta: dict[str, Any] | None,
) -> McpAppRuntimeContext | None:
    if config is None or tool_meta is None:
        return None
    resource_uri = tool_meta.get("ui", {}).get("resourceUri")
    required = (
        config.get("conversation_id"),
        config.get("user_id"),
        config.get("agent_id"),
        config.get("credential_subject_user_id"),
        config.get("mcp_server_id"),
        config.get("mcp_tool_id"),
    )
    if not isinstance(resource_uri, str) or not all(isinstance(value, str) for value in required):
        return None
    try:
        ids = [uuid.UUID(value) for value in required]
    except ValueError:
        return None
    return McpAppRuntimeContext(
        conversation_id=ids[0],
        user_id=ids[1],
        agent_id=ids[2],
        credential_subject_user_id=ids[3],
        mcp_server_id=ids[4],
        mcp_tool_id=ids[5],
        remote_tool_name=str(config.get("mcp_tool_name") or config.get("name")),
        resource_uri=resource_uri,
        tool_meta=tool_meta,
    )


async def record_mcp_app_binding(
    context: McpAppRuntimeContext,
    run_id: str,
    tool_call_id: str,
    result_meta: dict[str, Any] | None,
    structured_content: dict[str, Any] | None,
) -> str | None:
    return await record_runtime_binding(
        context,
        run_id,
        tool_call_id,
        result_meta,
        structured_content,
    )


__all__ = ["ResultMetaInterceptor", "mcp_app_context", "record_mcp_app_binding"]
