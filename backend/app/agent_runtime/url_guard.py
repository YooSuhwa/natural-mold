"""SSRF guard for agent-supplied URLs (SEC-1).

Tool inputs are attacker-influenceable (prompt injection), so any URL a
builtin tool fetches must be validated before the request AND on every
redirect hop: scheme http/https only, and no target that resolves to a
private, loopback, link-local, or otherwise non-global address — this
blocks localhost, RFC-1918 ranges, and cloud metadata endpoints
(169.254.169.254).

Known limitation: resolve-then-fetch leaves a DNS-rebinding TOCTOU window;
closing it requires pinning the resolved IP into the connection, which
httpx does not expose per-request. The per-hop re-validation plus
non-global rejection covers the practical attack surface for a scraping
tool.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


class BlockedUrlError(Exception):
    """The URL failed SSRF validation and must not be fetched."""


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # ``is_global`` is False for private, loopback, link-local, reserved,
    # unspecified, and carrier-grade NAT ranges; multicast is excluded
    # explicitly because a handful of multicast blocks report as global.
    return not ip.is_global or ip.is_multicast


async def _resolve_host(host: str, port: int) -> list[str]:
    """Resolve ``host`` to its addresses without blocking the event loop."""

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def ensure_url_allowed(url: str) -> None:
    """Raise :class:`BlockedUrlError` unless ``url`` targets a public host."""

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise BlockedUrlError(f"scheme '{parsed.scheme or ''}' is not allowed")
    host = parsed.hostname
    if not host:
        raise BlockedUrlError("URL has no host")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_address(literal):
            raise BlockedUrlError(f"address {literal} is not publicly routable")
        return

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addresses = await _resolve_host(host, port)
    except OSError as exc:
        raise BlockedUrlError(f"host '{host}' could not be resolved") from exc
    if not addresses:
        raise BlockedUrlError(f"host '{host}' could not be resolved")
    for raw in addresses:
        try:
            resolved = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise BlockedUrlError(f"host '{host}' resolved to an invalid address") from exc
        if _is_blocked_address(resolved):
            raise BlockedUrlError(f"host '{host}' resolves to non-public address {resolved}")
