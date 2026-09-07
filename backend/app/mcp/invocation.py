"""One-shot MCP tool invocation helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import AnyUrl

from app.mcp.client import build_headers


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(by_alias=True, exclude_none=True))
        except TypeError:
            return _jsonable(model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


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
        async with _open_http_session(transport, url, merged_headers) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            structured = getattr(result, "structuredContent", None)
            if structured is None:
                structured = getattr(result, "structured_content", None)
            return {
                "success": True,
                "content": _jsonable(getattr(result, "content", None)),
                "structured_content": _jsonable(structured),
                "meta": _jsonable(getattr(result, "meta", None)),
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


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
        async with _open_http_session(transport, url, merged_headers) as session:
            await session.initialize()
            result = await session.read_resource(resource_uri)
            return {
                "success": True,
                "contents": _jsonable(getattr(result, "contents", None)),
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


@asynccontextmanager
async def _open_http_session(
    transport: str,
    url: str,
    headers: dict[str, Any],
) -> AsyncIterator[Any]:
    from mcp.client.session import ClientSession

    if transport == "sse":
        from mcp.client.sse import sse_client

        async with (
            sse_client(url, headers=headers or None) as (read, write),
            ClientSession(read, write) as session,
        ):
            yield session
        return

    from mcp.client.streamable_http import streamablehttp_client

    async with (
        streamablehttp_client(url, headers=headers or None) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        yield session


__all__ = ["call_mcp_tool_once", "read_mcp_resource_once"]
