"""F-11 (plan 11.9): SSRF pinning below the pool, proxies, address rules.

Network-free: the real httpx/httpcore pool runs on a recording network backend
(``httpcore.AsyncMockStream`` replies) and a stub resolver.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpcore
import httpx
import pytest

from app.config import settings
from app.outbound import client as outbound_client
from app.outbound.client import outbound_http_client, outbound_sync_http_client
from app.outbound.destination import OutboundBlockedError
from app.outbound.transport import GuardTransport, is_blocked_address

PUBLIC, PUBLIC_2 = "93.184.216.34", "93.184.216.35"
OK = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]


class Sockets(httpcore.AsyncNetworkBackend):
    def __init__(self, *, refuse: tuple[str, ...] = (), reply: list[bytes] | None = None):
        self.dialed: list[str] = []
        self._refuse, self._reply = refuse, reply or OK

    async def connect_tcp(self, host, port, timeout=None, **_):  # noqa: ANN001, ANN003, ASYNC109
        self.dialed.append(host)
        if host in self._refuse:
            raise httpcore.ConnectError(f"refused {host}")
        return httpcore.AsyncMockStream(list(self._reply))

    async def sleep(self, seconds: float) -> None:
        return None


def _resolver(*addresses: str):
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.fixture(autouse=True)
def _no_env_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbound_client, "get_environment_proxies", dict)
    monkeypatch.setattr(settings, "app_env", "production")  # no dev local defaults


async def test_pinning_dials_checked_ips_but_keeps_the_hostname_above_tcp() -> None:
    sockets = Sockets()
    async with outbound_http_client(
        ssrf_guard=True, network_backend=sockets, resolver=_resolver(PUBLIC)
    ) as client:
        response = await client.get("http://api.example.com/v1")
    assert sockets.dialed == [PUBLIC]
    assert response.request.url.host == "api.example.com"
    assert response.request.headers["host"] == "api.example.com"


async def test_private_and_mixed_answers_never_dial_a_private_address() -> None:
    sockets = Sockets()
    async with outbound_http_client(
        ssrf_guard=True, network_backend=sockets, resolver=_resolver("10.0.0.7", "127.0.0.1")
    ) as client:
        with pytest.raises(OutboundBlockedError, match="no public address"):
            await client.get("https://internal.example.com/")
        with pytest.raises(OutboundBlockedError):
            await client.get("http://169.254.169.254/latest/meta-data")
    assert sockets.dialed == []
    mixed = Sockets()
    async with outbound_http_client(
        ssrf_guard=True, network_backend=mixed, resolver=_resolver("10.0.0.7", PUBLIC)
    ) as client:
        await client.get("http://rebind.example.com/")
    assert mixed.dialed == [PUBLIC]


async def test_all_public_addresses_are_tried_in_order() -> None:
    sockets = Sockets(refuse=(PUBLIC,))
    async with outbound_http_client(
        ssrf_guard=True, network_backend=sockets, resolver=_resolver(PUBLIC, PUBLIC_2)
    ) as client:
        await client.get("http://api.example.com/")
    assert sockets.dialed == [PUBLIC, PUBLIC_2]


async def test_redirect_hops_are_dialed_through_the_same_check() -> None:
    redirect = [b"HTTP/1.1 302 Found\r\n", b"Location: http://169.254.169.254/x\r\n"]
    sockets = Sockets(reply=[*redirect, b"Content-Length: 0\r\n", b"\r\n"])
    async with outbound_http_client(
        ssrf_guard=True,
        follow_redirects=True,
        network_backend=sockets,
        resolver=_resolver(PUBLIC),
    ) as client:
        with pytest.raises(OutboundBlockedError):
            await client.get("http://api.example.com/start")
    assert sockets.dialed == [PUBLIC]


async def test_outbound_private_allowed_hosts_are_dialed_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "outbound_private_allowed_hosts", "auth.corp")
    sockets = Sockets()
    async with outbound_http_client(
        ssrf_guard=True, network_backend=sockets, resolver=_resolver("10.1.2.3")
    ) as client:
        await client.get("http://auth.corp/token")
    assert sockets.dialed == ["auth.corp"]


@pytest.mark.parametrize(
    ("address", "blocked"),
    [
        ("64:ff9b::a00:1", True),  # NAT64 of 10.0.0.1
        ("64:ff9b::5db8:d822", False),  # NAT64 of 93.184.216.34
        ("2002:a00:1::1", True),  # 6to4 of 10.0.0.1
        ("::ffff:169.254.169.254", True),
        ("fd00::1", True),
        ("2606:4700::1111", False),
    ],
)
def test_embedded_ipv4_forms_follow_the_ipv4_rule(address: str, blocked: bool) -> None:
    assert is_blocked_address(ipaddress.ip_address(address)) is blocked


def _routes(client: httpx.AsyncClient | httpx.Client, url: str) -> object:
    return client._transport_for_url(httpx.URL(url))  # noqa: SLF001


def test_environment_proxies_are_honoured_async_and_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxies = {"https://": "http://proxy.corp:3128", "all://*internal.corp": None}
    monkeypatch.setattr(outbound_client, "get_environment_proxies", lambda: proxies)
    async_client = outbound_http_client(ssrf_guard=True, https_only=True)
    proxied = _routes(async_client, "https://api.example.com/")
    assert isinstance(proxied, GuardTransport)
    inner: Any = proxied._inner  # noqa: SLF001
    assert isinstance(inner._pool, httpcore.AsyncHTTPProxy)  # noqa: SLF001
    direct = _routes(async_client, "https://svc.internal.corp/")
    assert direct is async_client._transport  # noqa: SLF001 (NO_PROXY: direct, pinned)
    sync_client = outbound_sync_http_client()
    sync_proxied = _routes(sync_client, "https://api.openai.com/")
    assert isinstance(sync_proxied._pool, httpcore.HTTPProxy)  # type: ignore[attr-defined]  # noqa: SLF001
    assert _routes(sync_client, "https://svc.internal.corp/") is sync_client._transport  # noqa: SLF001
    sync_client.close()


async def test_proxied_routes_keep_destination_checks_and_literal_ip_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outbound_client, "get_environment_proxies", lambda: {"all://": "http://proxy.corp:3128"}
    )
    async with outbound_http_client(ssrf_guard=True, https_only=True) as client:
        with pytest.raises(OutboundBlockedError, match="https"):
            await client.get("http://api.example.com/")
        with pytest.raises(OutboundBlockedError, match="no public address"):
            await client.get("https://169.254.169.254/")
