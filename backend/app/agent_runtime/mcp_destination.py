"""Credential destination checks for runtime MCP connections (plan 11.9, 11.4.1; F-11).

At call time a credentialed MCP config (``credential_destination`` from
``build_tools_config``) must target https (``CREDENTIAL_HTTP_ALLOWED_HOSTS`` excepted),
a host in the credential's ``allowed_hosts`` (empty: owner's own runs only) and, for
an OAuth token issued for an MCP resource, that resource's canonical origin
(``token_destination_allowed``, F-15). Blocked tools become stubs that return the
reason; the connection is never opened. The transport itself is an
``outbound_http_client`` that stays https-only and refuses cross-host redirects.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.credentials.mcp_oauth_spec import token_destination_allowed
from app.outbound.client import credentialed_mcp_options
from app.outbound.destination import CredentialDestination, OutboundBlockedError, check_destination
from app.tools.risk import attach_tool_risk, mcp_tool_risk

logger = logging.getLogger(__name__)


def mcp_destination_error(config: dict[str, Any]) -> str | None:
    destination = CredentialDestination.from_config(config.get("credential_destination"))
    if destination is None:  # fail closed: a credential without a destination never goes out
        return "credential destination missing" if config.get("credentials") else None
    url = str(config.get("mcp_server_url") or "")
    try:
        check_destination(url, destination)
    except OutboundBlockedError as exc:
        return str(exc)
    values = config.get("credentials")
    resource = values.get("resource") if isinstance(values, dict) else None
    if resource and not token_destination_allowed(resource=str(resource), request_url=url):
        return "the OAuth token was issued for another MCP server"
    return None


def _blocked_stub(name: str, reason: str) -> BaseTool:
    async def _call(**_: Any) -> str:
        return f"MCP tool '{name}' cannot send its credential to this server: {reason}"

    tool = StructuredTool.from_function(
        coroutine=_call, name=name, description=f"MCP tool (blocked): {name}"
    )
    return attach_tool_risk(tool, mcp_tool_risk(name))


def split_blocked(configs: list[dict]) -> tuple[list[dict], list[BaseTool]]:
    allowed: list[dict] = []
    stubs: list[BaseTool] = []
    for config in configs:
        reason = mcp_destination_error(config)
        if reason is None:
            allowed.append(config)
            continue
        name = str(config.get("mcp_tool_name") or config.get("name"))
        logger.warning("MCP tool %s blocked by credential destination: %s", name, reason)
        stubs.append(_blocked_stub(name, reason))
    return allowed, stubs


def transport_options(config: dict[str, Any]) -> dict[str, Any]:
    """Extra MCP connection options for a credentialed config."""

    return credentialed_mcp_options(config.get("credential_destination") is not None)


__all__ = ["mcp_destination_error", "split_blocked", "transport_options"]
