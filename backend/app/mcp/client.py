"""MCP client — connect, initialize, and list tools.

Streamable-HTTP transport is used for ``sse`` and ``streamable_http`` (the MCP
Python SDK has separate stdio plumbing — out of scope for the test endpoint).
Authentication tokens injected into headers come from the credential payload
via :func:`app.credentials.interpolation.resolve_deep`, so the same template
syntax used elsewhere applies.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.credentials.interpolation import resolve_deep
from app.mcp.domain import McpServerInfo, McpToolDescriptor


def build_headers(
    static_headers: dict[str, Any] | None,
    credentials: dict[str, Any] | None,
) -> dict[str, str]:
    """Merge static headers with credential interpolation. Returns ``{}`` if empty."""

    if not static_headers:
        return {}
    resolved = resolve_deep(dict(static_headers), credentials or {})
    out: dict[str, str] = {}
    for key, value in resolved.items():
        if value is None:
            continue
        out[str(key)] = str(value)
    return out


def build_env_vars(
    static_env: dict[str, Any] | None,
    credentials: dict[str, Any] | None,
) -> dict[str, str]:
    """Resolve ``${credential.<field>}`` style markers in env_vars.

    The credential payload is used as the interpolation namespace; missing
    fields raise :class:`InterpolationError` from
    :mod:`app.credentials.interpolation`.
    """

    if not static_env:
        return {}
    resolved = resolve_deep(dict(static_env), credentials or {})
    return {str(k): "" if v is None else str(v) for k, v in resolved.items()}


async def connect_and_list(
    *,
    transport: str,
    url: str | None,
    headers: dict[str, Any] | None = None,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Probe an MCP server and return ``{success, server_info, tools, error}``.

    Stdio transport is reported as unsupported for now — the discover/test
    endpoints only need network transports for the PoC.
    """

    if transport not in {"sse", "streamable_http"}:
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"transport '{transport}' is not supported by the probe yet",
        }
    if not url:
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "url is required for sse / streamable_http transports",
        }

    merged_headers = build_headers(headers, credentials)

    try:
        # Imported lazily so the test suite can monkey-patch the symbols on this
        # module without forcing the heavy SDK import at collection time.
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:  # pragma: no cover — runtime dep present in pyproject
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"mcp client SDK unavailable: {exc}",
        }

    try:
        async with (
            streamablehttp_client(url, headers=merged_headers or None) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            init_result = await session.initialize()
            tools_result = await session.list_tools()

            info = McpServerInfo()
            if init_result.serverInfo is not None:
                info = McpServerInfo(
                    name=init_result.serverInfo.name,
                    version=init_result.serverInfo.version,
                )
            descriptors = [
                McpToolDescriptor(
                    name=t.name,
                    description=t.description or "",
                    input_schema=dict(t.inputSchema or {}),
                )
                for t in tools_result.tools
            ]
            return {
                "success": True,
                "server_info": asdict(info),
                "tools": [asdict(d) for d in descriptors],
                "error": None,
            }
    except Exception as exc:  # noqa: BLE001 — surface as soft error to the UI
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": str(exc),
        }


__all__ = ["build_env_vars", "build_headers", "connect_and_list"]
