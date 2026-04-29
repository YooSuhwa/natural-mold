"""MCP domain — server connection, tool discovery, OAuth2 helpers.

Public surface:
- :class:`McpToolDescriptor` — normalized tool metadata returned by ``list_tools``.
- :func:`connect_and_list` — single-shot probe used by the test endpoint.
- :func:`discover_tools` — normalize + persist server tool list.
"""

from app.mcp.client import McpToolDescriptor, connect_and_list
from app.mcp.discovery import discover_tools

__all__ = [
    "McpToolDescriptor",
    "connect_and_list",
    "discover_tools",
]
