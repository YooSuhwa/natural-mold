"""One-shot MCP tool invocation helpers (MCP Apps follow-up calls and resource reads)."""

from __future__ import annotations

from typing import Any

from app.mcp import sdk
from app.mcp.client import build_headers


def _jsonable(value: Any) -> Any:
    return sdk.jsonable(value)


async def call_mcp_tool_once(
    *,
    transport: str,
    url: str | None,
    headers: dict[str, Any] | None,
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if transport not in {"sse", "streamable_http"}:
        return {"success": False, "error": f"transport '{transport}' is not supported"}
    if not url:
        return {"success": False, "error": "url is required"}

    merged_headers = build_headers(headers, credentials)
    try:
        result = await sdk.call_tool(
            sdk.http_transport(
                transport, url, merged_headers or None, credentialed=credentials is not None
            ),
            tool_name,
            arguments,
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "content": _jsonable(result.content),
        "structured_content": _jsonable(result.structured_content),
        "meta": _jsonable(result.meta),
    }


async def read_mcp_resource_once(
    *,
    transport: str,
    url: str | None,
    headers: dict[str, Any] | None,
    resource_uri: str,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one MCP resource through the existing authenticated connection."""

    if transport not in {"sse", "streamable_http"}:
        return {
            "success": False,
            "error": f"resource reads are not supported for transport '{transport}'",
        }
    if not url:
        return {"success": False, "error": "url is required"}

    merged_headers = build_headers(headers, credentials)
    try:
        contents = await sdk.read_resource(
            sdk.http_transport(
                transport, url, merged_headers or None, credentialed=credentials is not None
            ),
            resource_uri,
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    return {"success": True, "contents": _jsonable(contents)}


__all__ = ["call_mcp_tool_once", "read_mcp_resource_once"]
