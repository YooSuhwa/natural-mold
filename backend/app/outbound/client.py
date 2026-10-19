"""``outbound_http_client()`` — the one way to build an outbound httpx client (11.9).

Always the shared trust store (``http_ssl.get_outbound_ssl_context``; OS store plus the
in-house CA bundle), an explicit timeout and the guarded transport. Never
``verify=False``: ``tests/authz/test_f11_migration_and_ratchet.py`` pins direct
construction.

Environment proxies (``HTTP(S)_PROXY``/``ALL_PROXY``/``NO_PROXY``) are honoured the way
httpx does it (``get_environment_proxies``); because an explicit transport disables
httpx's own lookup, the same mounts are rebuilt here for both the async and the sync
client. Proxied routes keep the destination/https checks but skip IP pinning (the proxy
resolves the name); ``NO_PROXY`` hosts go direct and are pinned.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpcore
import httpx
from httpx._utils import get_environment_proxies

from app.http_ssl import get_outbound_ssl_context
from app.outbound.destination import OutboundBlockedError
from app.outbound.transport import GuardTransport, PinnedBackend, Resolver, pin

DEFAULT_TIMEOUT = 30.0


async def _same_host_redirects_only(response: httpx.Response) -> None:
    if not response.is_redirect:
        return
    location = response.headers.get("location")
    if location is None:
        return
    target = response.request.url.join(location)
    if (target.host, target.port) != (response.request.url.host, response.request.url.port):
        raise OutboundBlockedError(f"redirect to another host '{target.host}' blocked")


def _proxy_mounts[T](build: Callable[[str], T]) -> dict[str, T | None]:
    return {
        pattern: (build(proxy) if proxy else None)
        for pattern, proxy in get_environment_proxies().items()
    }


def outbound_http_client(
    *,
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
    https_only: bool = False,
    ssrf_guard: bool = False,
    follow_redirects: bool = False,
    same_host_redirects: bool = False,
    headers: dict[str, str] | None = None,
    auth: httpx.Auth | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient``.

    ``https_only`` — credentialed calls (``CREDENTIAL_HTTP_ALLOWED_HOSTS`` excepted).
    ``ssrf_guard`` — public addresses only, TCP pinned to the checked address.
    ``same_host_redirects`` — a redirect to another host raises instead of following.
    Test seams: ``transport`` replaces the wire (no proxies, no pinning; the guard still
    runs); ``network_backend``/``resolver`` replace sockets/DNS under the real pool.
    """

    ctx = get_outbound_ssl_context()
    mounts: dict[str, Any] = {}
    if transport is None:
        direct = httpx.AsyncHTTPTransport(verify=ctx)
        if ssrf_guard:
            pin(direct, PinnedBackend(network_backend, resolver))
        elif network_backend is not None:
            pin(direct, network_backend)
        transport = direct
        mounts = _proxy_mounts(
            lambda proxy: GuardTransport(
                httpx.AsyncHTTPTransport(verify=ctx, proxy=proxy),
                https_only=https_only,
                literal_check=ssrf_guard,
            )
        )
    hooks: dict[str, list[Any]] = {}
    if same_host_redirects:
        hooks["response"] = [_same_host_redirects_only]
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers=headers,
        auth=auth,
        event_hooks=hooks,
        transport=GuardTransport(transport, https_only=https_only),
        mounts=mounts,
    )


def mcp_http_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """MCP SDK ``httpx_client_factory`` for credentialed remote MCP transports."""

    return outbound_http_client(
        timeout=timeout or httpx.Timeout(DEFAULT_TIMEOUT),
        https_only=True,
        follow_redirects=True,  # the MCP SDK default
        same_host_redirects=True,
        headers=headers,
        auth=auth,
    )


def credentialed_mcp_options(credentialed: bool) -> dict[str, Any]:
    """``streamablehttp_client``/``sse_client`` kwargs for a credentialed connection."""

    return {"httpx_client_factory": mcp_http_client_factory} if credentialed else {}


def outbound_sync_http_client(*, timeout: float | httpx.Timeout = DEFAULT_TIMEOUT) -> httpx.Client:
    """Sync twin for SDKs that need one (model clients): same trust store and proxies."""

    ctx = get_outbound_ssl_context()
    return httpx.Client(
        timeout=timeout,
        transport=httpx.HTTPTransport(verify=ctx),
        mounts=_proxy_mounts(lambda proxy: httpx.HTTPTransport(verify=ctx, proxy=proxy)),
    )


__all__ = [
    "DEFAULT_TIMEOUT",
    "credentialed_mcp_options",
    "mcp_http_client_factory",
    "outbound_http_client",
    "outbound_sync_http_client",
]
