"""Guarded outbound transport: credential destination, https-only and SSRF pinning.

``GuardTransport`` checks every request (each redirect hop is its own request) above
the connection pool, so no runner can skip it:

1. a credential destination bound by ``destination.bind_destination``;
2. ``https_only`` (credentialed clients; ``CREDENTIAL_HTTP_ALLOWED_HOSTS`` excepted).

The SSRF guard lives one layer lower, in ``PinnedBackend.connect_tcp``: the URL, the
pool key, ``Host``, SNI and certificate checks all keep the hostname, while the TCP
connection goes only to an address resolved and checked right there (closes the
resolve-then-connect DNS rebinding gap of ``agent_runtime.url_guard``). Public
addresses are tried in resolver order; private, loopback, link-local (metadata),
reserved and NAT64/6to4/IPv4-mapped forms of those are never dialed.
``OUTBOUND_PRIVATE_ALLOWED_HOSTS`` are exempt. Through an environment proxy the proxy
resolves the name, so pinning is skipped there and only IP-literal targets are
checked (``literal_check``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, cast

import httpcore
import httpx

from app.outbound.destination import OutboundBlockedError, check_bound_destination, require_https
from app.outbound.hosts import is_ip_literal, normalize_host, private_allowed

Resolver = Callable[[str, int], Awaitable[list[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(ip: IPAddress) -> ipaddress.IPv4Address | None:
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_blocked_address(ip: IPAddress) -> bool:
    # ``is_global`` is False for private, loopback, link-local (169.254.169.254 metadata),
    # reserved, unspecified and carrier-grade NAT ranges; multicast is excluded explicitly.
    embedded = _embedded_ipv4(ip)
    if embedded is not None and is_blocked_address(embedded):
        return True
    return not ip.is_global or ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _public(host: str, raw: Iterable[str]) -> list[str]:
    public: list[str] = []
    for value in raw:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not is_blocked_address(address):
            public.append(str(address))
    if not public:
        raise OutboundBlockedError(f"host '{host}' resolves to no public address")
    return public


async def public_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    """Public addresses of ``host`` in resolver order; raise when there are none."""

    if is_ip_literal(host):
        return _public(host, [host])
    try:
        resolved = await resolver(host, port)
    except OSError as exc:
        raise OutboundBlockedError(f"host '{host}' could not be resolved") from exc
    return _public(host, resolved)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Dial only checked public addresses; the hostname stays everywhere above TCP."""

    def __init__(
        self, inner: httpcore.AsyncNetworkBackend | None = None, resolver: Resolver | None = None
    ) -> None:
        self._inner: httpcore.AsyncNetworkBackend = inner or cast(
            httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend()
        )
        self._resolver = resolver or system_resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore backend interface
        **kwargs: Any,
    ) -> httpcore.AsyncNetworkStream:
        if private_allowed(host):
            return await self._inner.connect_tcp(host, port, timeout, **kwargs)
        last: Exception | None = None
        for address in await public_addresses(normalize_host(host), port, self._resolver):
            try:
                return await self._inner.connect_tcp(address, port, timeout, **kwargs)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        if last is None:  # unreachable: public_addresses never returns an empty list
            raise OutboundBlockedError(f"host '{host}' has no address to dial")
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore backend interface
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise OutboundBlockedError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pin(transport: httpx.AsyncHTTPTransport, backend: httpcore.AsyncNetworkBackend) -> None:
    """Install ``backend`` on the transport's pool (httpx exposes no parameter for it)."""

    pool = transport._pool  # noqa: SLF001
    if not hasattr(pool, "_network_backend"):  # fail loudly if httpcore changes layout
        raise RuntimeError("httpcore connection pool has no _network_backend")
    pool._network_backend = backend  # noqa: SLF001


class GuardTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        https_only: bool = False,
        literal_check: bool = False,
    ) -> None:
        self._inner = inner
        self._https_only = https_only
        self._literal_check = literal_check

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = normalize_host(url.host)
        check_bound_destination(url)
        if self._https_only:
            require_https(url.scheme, host)
        if url.scheme not in {"http", "https"}:
            raise OutboundBlockedError(f"scheme '{url.scheme}' is not allowed")
        if self._literal_check and is_ip_literal(host) and not private_allowed(host):
            _public(host, [host])
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


__all__ = [
    "GuardTransport",
    "PinnedBackend",
    "Resolver",
    "is_blocked_address",
    "pin",
    "public_addresses",
]
