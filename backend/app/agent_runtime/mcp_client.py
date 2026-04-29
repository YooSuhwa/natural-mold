"""Thin compatibility wrapper around :mod:`app.mcp.client`.

The agent runtime historically owned its own MCP probe implementation. M5
collapses it to delegating into the new ``app.mcp.client`` so credential
interpolation lives in a single module
(``app.credentials.interpolation.resolve_deep``).

Two helpers stay here for backwards compatibility:

- :func:`extract_transport_headers` — coerces a connection-style ``extra_config``
  dict into a clean ``Mapping[str, str]``.
- :func:`test_mcp_connection` — convenience wrapper used by older test paths.

New code should call :func:`app.mcp.client.connect_and_list` directly.
"""

from __future__ import annotations

from typing import Any

from app.mcp import client as _mcp_client


def extract_transport_headers(
    extra_config: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Strip non-string entries from a connection's headers dict."""

    if not extra_config:
        return None
    headers = extra_config.get("headers")
    if not isinstance(headers, dict):
        return None
    cleaned = {k: v for k, v in headers.items() if isinstance(v, str)}
    return cleaned or None


async def test_mcp_connection(
    url: str,
    auth_config: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Probe an MCP server via the unified ``app.mcp.client`` path."""

    headers: dict[str, str] = {}
    if extra_headers:
        headers.update(extra_headers)
    if auth_config and auth_config.get("api_key"):
        header_name = auth_config.get("header_name", "Authorization")
        headers[header_name] = auth_config["api_key"]

    return await _mcp_client.connect_and_list(
        transport="streamable_http",
        url=url,
        headers=headers or None,
        credentials=None,
    )


__all__ = ["extract_transport_headers", "test_mcp_connection"]
