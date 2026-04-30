"""MCP client — connect, initialize, and list tools.

Streamable-HTTP transport is used for ``sse`` and ``streamable_http`` (the MCP
Python SDK has separate stdio plumbing — out of scope for the test endpoint).
Authentication tokens injected into headers come from the credential payload
via :func:`app.credentials.interpolation.resolve_deep`, so the same template
syntax used elsewhere applies.
"""

from __future__ import annotations

import time
import uuid as _uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from app.credentials.interpolation import resolve_deep
from app.hooks import HookContext, HookResult, hooks
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
    user_id: _uuid.UUID | None = None,
    mcp_server_id: _uuid.UUID | None = None,
    credential_id: _uuid.UUID | None = None,
) -> dict[str, Any]:
    """Probe an MCP server and return ``{success, server_info, tools, error}``.

    Stdio transport is reported as unsupported for now — the discover/test
    endpoints only need network transports for the PoC. Hook dispatch fires
    only when a ``user_id`` is provided so the test suite (which calls the
    helper directly) is unaffected.
    """

    hook_ctx = _build_mcp_hook_context(
        user_id=user_id,
        mcp_server_id=mcp_server_id,
        credential_id=credential_id,
        url=url,
        transport=transport,
    )
    if hook_ctx is not None:
        await hooks.run_pre(hook_ctx)
    started = time.monotonic()

    if transport not in {"sse", "streamable_http"}:
        result = {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"transport '{transport}' is not supported by the probe yet",
        }
        if hook_ctx is not None:
            await hooks.run_post(
                hook_ctx,
                HookResult(duration_ms=int((time.monotonic() - started) * 1000)),
            )
        return result
    if not url:
        result = {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "url is required for sse / streamable_http transports",
        }
        if hook_ctx is not None:
            await hooks.run_post(
                hook_ctx,
                HookResult(duration_ms=int((time.monotonic() - started) * 1000)),
            )
        return result

    merged_headers = build_headers(headers, credentials)

    try:
        # Imported lazily so the test suite can monkey-patch the symbols on this
        # module without forcing the heavy SDK import at collection time.
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:  # pragma: no cover — runtime dep present in pyproject
        result = {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"mcp client SDK unavailable: {exc}",
        }
        if hook_ctx is not None:
            await hooks.run_post(
                hook_ctx,
                HookResult(duration_ms=int((time.monotonic() - started) * 1000)),
            )
        return result

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
            result = {
                "success": True,
                "server_info": asdict(info),
                "tools": [asdict(d) for d in descriptors],
                "error": None,
            }
    except Exception as exc:  # noqa: BLE001 — surface as soft error to the UI
        if hook_ctx is not None:
            await hooks.run_failure(hook_ctx, exc)
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": str(exc),
        }

    if hook_ctx is not None:
        await hooks.run_post(
            hook_ctx,
            HookResult(
                duration_ms=int((time.monotonic() - started) * 1000),
                output=f"tools={len(result.get('tools') or [])}",
            ),
        )
    return result


def _build_mcp_hook_context(
    *,
    user_id: _uuid.UUID | None,
    mcp_server_id: _uuid.UUID | None,
    credential_id: _uuid.UUID | None,
    url: str | None,
    transport: str,
) -> HookContext | None:
    if user_id is None:
        return None
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="mcp_call",
        user_id=user_id,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        mcp_server_id=mcp_server_id,
        credential_id=credential_id,
        metadata={"transport": transport, "url": url},
    )


__all__ = ["build_env_vars", "build_headers", "connect_and_list"]
