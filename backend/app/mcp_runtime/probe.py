"""Check the MCP runtime end to end: health, then a signed call to its diagnostics server.

The backend signs a token for ``system/diagnostics``, calls ``whoami`` with its own MCP
client and expects its run id back, which proves the URL, the shared secret and the
protocol between the two. Used by the operator's system status (``app.install``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from app.config import settings
from app.mcp import sdk
from app.mcp_runtime import tokens
from app.outbound.client import outbound_http_client

DIAGNOSTICS: Final = "system/diagnostics"
_TIMEOUT: Final = 5.0


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    configured: bool
    ok: bool
    detail: str


async def check_runtime() -> RuntimeHealth:
    base = settings.mcp_runtime_url.rstrip("/")
    if not base:
        return RuntimeHealth(False, False, "MCP_RUNTIME_URL is not set")
    try:
        async with outbound_http_client(timeout=_TIMEOUT) as http:
            response = await http.get(f"{base}/healthz")
        if response.status_code != 200:  # noqa: PLR2004
            return RuntimeHealth(True, False, f"health check answered {response.status_code}")
    except Exception as exc:  # noqa: BLE001 - reported as status, never raised
        return RuntimeHealth(True, False, f"not reachable ({type(exc).__name__})")
    probe = tokens.RunIdentity(
        actor_id=uuid.uuid4(), run_id=uuid.uuid4(), tenant_id=uuid.uuid4(), org_id=uuid.uuid4()
    )
    try:
        token = tokens.issue(DIAGNOSTICS, probe, lifetime_seconds=60)
    except tokens.RuntimeTokenUnavailable as exc:
        return RuntimeHealth(True, False, str(exc))
    try:
        echoed = await _whoami(f"{base}/mcp/{DIAGNOSTICS}/", token)
    except Exception as exc:  # noqa: BLE001
        return RuntimeHealth(True, False, f"signed call failed ({type(exc).__name__})")
    if echoed != str(probe.run_id):
        return RuntimeHealth(True, False, "the runtime answered for another run")
    return RuntimeHealth(True, True, "signed call verified")


async def _whoami(url: str, token: str) -> str | None:
    result = await sdk.call_tool(
        sdk.runtime_transport(url, {"Authorization": f"Bearer {token}"}),
        "whoami",
        {},
        request_timeout=_TIMEOUT,
    )
    content = result.structured_content or {}
    # FastMCP wraps a non-object return in {"result": ...}; whoami returns an object.
    value = content.get("run_id") if isinstance(content, dict) else None
    return str(value) if value else None
