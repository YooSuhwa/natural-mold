"""The backend's side of the MCP gateway (plan 8.2, decision D10; mcp-runtime ``relay.py``).

With ``MCP_GATEWAY_ENABLED`` no part of the backend connects to an external MCP server:
``app.mcp.sdk`` sends every Streamable HTTP and SSE request to the runtime's relay, and the
OAuth metadata of an MCP server is read through it too (``get_json``). Each request carries
a fresh relay token that seals the upstream URL and headers with what the connection may do
(``GatewayScope``). When the gateway does not answer, the call fails; nothing falls back to
a direct connection.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from app.config import settings
from app.mcp_runtime import tokens
from app.outbound.client import outbound_http_client
from app.outbound.destination import require_https
from app.outbound.hosts import plaintext_allowed

_NIL: Final = uuid.UUID(int=0)
# Minted per request; a minute covers one request's start.
TOKEN_SECONDS: Final = 60
_TIMEOUT: Final = 15.0


class GatewayUnavailable(RuntimeError):
    """MCP_GATEWAY_ENABLED is on but the runtime is not configured."""


@dataclass(frozen=True, slots=True)
class GatewayScope:
    """What one connection may do through the gateway (sealed into its relay tokens).

    ``tools``: the names ``tools/call`` may use (empty: none, e.g. discovery). ``resources``:
    ``resources/read`` allowed (MCP Apps). The ids name who the call runs for; unknown ones
    travel as the nil UUID.
    """

    tools: tuple[str, ...] = ()
    resources: bool = False
    actor_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Relay:
    """Where one upstream connection goes instead, and how to sign each request."""

    url: str
    scope: GatewayScope
    upstream: dict[str, Any]

    def token(self) -> str:
        scope = self.scope
        identity = tokens.RunIdentity(
            actor_id=scope.actor_id or _NIL,
            run_id=scope.run_id or uuid.uuid4(),
            tenant_id=scope.tenant_id or _NIL,
            org_id=scope.org_id or _NIL,
            agent_id=scope.agent_id,
        )
        return tokens.issue(
            tokens.RELAY, identity, lifetime_seconds=TOKEN_SECONDS, upstream=self.upstream
        )


def enabled() -> bool:
    return bool(settings.mcp_gateway_enabled)


def relay(
    transport: str,
    url: str,
    headers: Mapping[str, str] | None,
    *,
    credentialed: bool,
    scope: GatewayScope,
) -> Relay:
    """The relay for one upstream connection (credentialed: https only, as directly)."""
    base = settings.mcp_runtime_url.rstrip("/")
    if not base:
        raise GatewayUnavailable("MCP_GATEWAY_ENABLED needs MCP_RUNTIME_URL")
    target = urlsplit(url)
    host = target.hostname or ""
    if credentialed:
        require_https(target.scheme, host)
    upstream = {
        "url": url,
        "transport": transport,
        "headers": {str(k): str(v) for k, v in (headers or {}).items()},
        "tools": list(scope.tools),
        "resources": scope.resources,
        "https_only": credentialed and not plaintext_allowed(host),
    }
    path = f"{base}/relay{target.path or '/'}" + (f"?{target.query}" if target.query else "")
    return Relay(path, scope, upstream)


async def get_json(url: str) -> Any:
    """GET one JSON document from an MCP server's origin through the relay (OAuth metadata).

    Refuses redirects and plain http, like the direct metadata client.
    """
    route = relay("streamable_http", url, None, credentialed=True, scope=GatewayScope())
    async with outbound_http_client(timeout=_TIMEOUT) as client:
        response = await client.get(route.url, headers={"Authorization": f"Bearer {route.token()}"})
    response.raise_for_status()
    return response.json()


__all__ = ["GatewayScope", "GatewayUnavailable", "Relay", "enabled", "get_json", "relay"]
