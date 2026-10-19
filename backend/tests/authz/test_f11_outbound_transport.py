"""F-11 (plan 11.9): host patterns, https-only, redirects and bound destinations.

Network-free: an ``httpx.MockTransport`` stands in for the wire; the guard runs in
front of it. SSRF pinning and proxies: ``test_f11_ssrf_pinning.py``.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.outbound.client import outbound_http_client
from app.outbound.destination import (
    CredentialDestination,
    OutboundBlockedError,
    bind_destination,
)
from app.outbound.hosts import host_matches, validate_host_pattern


class Wire:
    def __init__(self, respond=None) -> None:  # noqa: ANN001
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda request: httpx.Response(200, json={"ok": True}))

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._respond(request)

        return httpx.MockTransport(handler)


def _client(wire: Wire, **options) -> httpx.AsyncClient:  # noqa: ANN003
    return outbound_http_client(transport=wire.transport(), **options)


@pytest.mark.parametrize(
    ("host", "pattern", "expected"),
    [
        ("a.example.com", "*.example.com", True),
        ("A.Example.COM.", "*.example.com", True),
        ("example.com", "*.example.com", False),
        ("a.b.example.com", "*.example.com", False),
        ("badexample.com", "*.example.com", False),
        ("badexample.com", "example.com", False),
        ("api.example.com", "api.example.com", True),
    ],
)
def test_wildcards_match_on_label_boundaries(host: str, pattern: str, expected: bool) -> None:
    assert host_matches(host, pattern) is expected


@pytest.mark.parametrize(
    "bad", ["https://a.com", "a.com/path", "a.com:443", "*.10.0.0.1", "*.com", "a.*.com", ""]
)
def test_host_patterns_reject_schemes_paths_ports_ips_and_inner_wildcards(bad: str) -> None:
    with pytest.raises(ValueError, match="host|label|IP|wildcard"):
        validate_host_pattern(bad)


def test_host_patterns_normalize_and_accept_exact_ips() -> None:
    assert validate_host_pattern(" API.Example.com. ") == "api.example.com"
    assert validate_host_pattern("*.example.com") == "*.example.com"
    assert validate_host_pattern("10.0.0.1") == "10.0.0.1"
    assert validate_host_pattern("[2001:db8::1]") == "2001:db8::1"
    assert host_matches("10.0.0.1", "10.0.0.1") and not host_matches("10.0.0.2", "10.0.0.1")


async def test_https_only_refuses_http_unless_the_operator_allows_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire()
    monkeypatch.setattr(settings, "app_env", "production")
    async with _client(wire, https_only=True) as client:
        with pytest.raises(OutboundBlockedError, match="https"):
            await client.get("http://localhost:8080/api")  # dev default off in production
        with pytest.raises(OutboundBlockedError, match="https"):
            await client.get("http://intranet.corp/api")
        monkeypatch.setattr(settings, "credential_http_allowed_hosts", "intranet.corp")
        await client.get("http://intranet.corp/api")
    assert len(wire.requests) == 1


async def test_same_host_redirects_refuse_another_host() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.example.com":
            return httpx.Response(307, headers={"location": "https://evil.example.net/mcp"})
        return httpx.Response(200)

    wire = Wire(respond)
    async with _client(wire, follow_redirects=True, same_host_redirects=True) as client:
        with pytest.raises(OutboundBlockedError, match="another host"):
            await client.post("https://mcp.example.com/mcp", headers={"Authorization": "t"})
    assert [r.url.host for r in wire.requests] == ["mcp.example.com"]


async def test_a_bound_destination_blocks_hosts_outside_allowed_hosts() -> None:
    wire = Wire()
    destination = CredentialDestination(("api.example.com", "*.cdn.example.com"), False)
    async with _client(wire) as client:
        with bind_destination(destination):
            await client.get("https://api.example.com/a")
            await client.get("https://eu.cdn.example.com/b")
            with pytest.raises(OutboundBlockedError, match="allowed hosts"):
                await client.get("https://evil.example.net/steal")
        await client.get("https://evil.example.net/no-credential")  # unbound again
    assert [r.url.host for r in wire.requests] == [
        "api.example.com",
        "eu.cdn.example.com",
        "evil.example.net",
    ]


async def test_a_bound_destination_blocks_redirects_off_the_allowed_hosts() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.com" and request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://evil.example.net/x"})
        if request.url.path == "/b":
            return httpx.Response(302, headers={"location": "http://api.example.com/c"})
        return httpx.Response(200)

    wire = Wire(respond)
    destination = CredentialDestination(("api.example.com",), False)
    async with _client(wire, follow_redirects=True) as client:
        with bind_destination(destination):
            with pytest.raises(OutboundBlockedError, match="allowed hosts"):
                await client.get("https://api.example.com/a")
            with pytest.raises(OutboundBlockedError, match="https"):  # https -> http
                await client.get("https://api.example.com/b")
    assert [r.url.path for r in wire.requests] == ["/a", "/b"]


async def test_empty_allowed_hosts_only_go_out_in_the_owners_own_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "credential_empty_hosts_policy", "enforce")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.com":
            return httpx.Response(302, headers={"location": "https://other.example.net/"})
        return httpx.Response(200)

    wire = Wire(respond)
    async with _client(wire, follow_redirects=True) as client:
        with (
            bind_destination(CredentialDestination((), False)),
            pytest.raises(OutboundBlockedError, match="no allowed hosts"),
        ):
            await client.get("https://api.example.com/")
        assert wire.requests == []
        with (
            bind_destination(CredentialDestination((), True)),
            pytest.raises(OutboundBlockedError, match="another host"),
        ):
            await client.get("https://api.example.com/")
    assert [r.url.host for r in wire.requests] == ["api.example.com"]


async def test_empty_allowed_hosts_warn_by_default_and_still_pin_the_host(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert settings.credential_empty_hosts_policy == "warn"
    wire = Wire()
    async with _client(wire) as client:
        with bind_destination(CredentialDestination((), False)):
            await client.get("https://api.example.com/")
            with pytest.raises(OutboundBlockedError, match="another host"):
                await client.get("https://other.example.net/")
    assert [r.url.host for r in wire.requests] == ["api.example.com"]
    assert "credential_empty_hosts" in caplog.text


def test_dev_defaults_allow_local_http_and_private_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.outbound.hosts import plaintext_allowed, private_allowed

    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "credential_http_allowed_hosts", "")
    monkeypatch.setattr(settings, "outbound_private_allowed_hosts", "")
    for host in ("localhost", "127.0.0.1", "host.docker.internal"):
        assert plaintext_allowed(host) and private_allowed(host)
    monkeypatch.setattr(settings, "credential_http_allowed_hosts", "intranet.corp")
    assert plaintext_allowed("intranet.corp") and not plaintext_allowed("localhost")
    assert private_allowed("localhost")  # the two settings are independent
    monkeypatch.setattr(settings, "app_env", "production")
    assert not private_allowed("localhost")
