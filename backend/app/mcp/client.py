"""MCP client — connect, initialize, and list tools.

Three transports supported:

* ``streamable_http`` — uses ``mcp.client.streamable_http.streamablehttp_client``
* ``sse`` — same path as ``streamable_http`` (the MCP SDK exposes both via the
  same client; the registry entry decides which URL shape is sent)
* ``stdio`` — launches a local subprocess using
  ``mcp.client.stdio.stdio_client`` with ``command`` / ``args`` / ``env_vars``
  pulled from the :class:`McpServer` row

Authentication tokens injected into headers / env_vars come from the
credential payload via :func:`app.credentials.interpolation.resolve_deep`, so
the same template syntax used elsewhere applies.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.credentials.interpolation import resolve_deep
from app.hooks import HookContext, HookResult, hooks
from app.mcp.domain import McpServerInfo, McpToolDescriptor

MOLDY_CREDENTIAL_HEADER = "X-Moldy-Credential"


def build_headers(
    static_headers: dict[str, Any] | None,
    credentials: dict[str, Any] | None,
) -> dict[str, str]:
    """Merge static headers with credential interpolation. Returns ``{}`` if empty."""

    credential_payload = credentials or {}
    resolved = resolve_deep(dict(static_headers or {}), credential_payload)
    out: dict[str, str] = {}
    for key, value in resolved.items():
        if value is None:
            continue
        out[str(key)] = str(value)
    if credential_payload.get("secret") is not None and not any(
        key.lower() == MOLDY_CREDENTIAL_HEADER.lower() for key in out
    ):
        out[MOLDY_CREDENTIAL_HEADER] = str(credential_payload["secret"])
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
    command: str | None = None,
    args: list[Any] | None = None,
    env_vars: dict[str, Any] | None = None,
    user_id: _uuid.UUID | None = None,
    mcp_server_id: _uuid.UUID | None = None,
    credential_id: _uuid.UUID | None = None,
) -> dict[str, Any]:
    """Probe an MCP server and return ``{success, server_info, tools, error}``.

    Supports ``stdio`` (local subprocess) and ``sse`` / ``streamable_http``
    (network) transports. ``stdio`` requires ``command``; the network
    transports require ``url``. Hook dispatch fires only when a ``user_id`` is
    provided so the test suite (which calls the helper directly) is unaffected.
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

    if transport == "stdio":
        result = await _connect_stdio(
            command=command,
            args=args,
            env_vars=env_vars,
            credentials=credentials,
        )
        if hook_ctx is not None:
            if result.get("success"):
                await hooks.run_post(
                    hook_ctx,
                    HookResult(
                        duration_ms=int((time.monotonic() - started) * 1000),
                        output=f"tools={len(result.get('tools') or [])}",
                    ),
                )
            else:
                await hooks.run_post(
                    hook_ctx,
                    HookResult(duration_ms=int((time.monotonic() - started) * 1000)),
                )
        return result

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


async def _connect_stdio(
    *,
    command: str | None,
    args: list[Any] | None,
    env_vars: dict[str, Any] | None,
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run an MCP probe over a local stdio child process.

    Wraps the SDK ``stdio_client`` in :func:`asyncio.wait_for` so a stuck
    server (e.g. waiting on missing env vars) can't hang the request handler.
    Timeout is taken from ``settings.mcp_connection_timeout``.
    """

    if not command:
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "stdio transport requires command",
        }

    try:
        # Lazy import so the heavy SDK isn't pulled in at module load time
        # (matches the streamable_http branch). The same path lets tests
        # monkeypatch the symbol on this module.
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:  # pragma: no cover — runtime dep present
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"mcp client SDK unavailable: {exc}",
        }

    resolved_env = build_env_vars(env_vars, credentials)
    server_params = StdioServerParameters(
        command=command,
        args=[str(a) for a in (args or [])],
        env=resolved_env or None,
    )

    timeout_s = max(int(getattr(settings, "mcp_connection_timeout", 10)), 1)

    async def _run() -> dict[str, Any]:
        async with (
            stdio_client(server_params) as (read, write),
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

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_s)
    except TimeoutError:
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": f"stdio probe timed out after {timeout_s}s",
        }
    except Exception as exc:  # noqa: BLE001 — soft error to UI
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": str(exc),
        }


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


__all__ = [
    "MOLDY_CREDENTIAL_HEADER",
    "build_env_vars",
    "build_headers",
    "connect_and_list",
]
