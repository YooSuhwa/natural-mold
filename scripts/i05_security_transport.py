"""I-05 transport, cookie, header and RLS on/off parity checks (plan 16.6, 15.2.1)."""

from __future__ import annotations

import socket
import ssl
from collections.abc import Mapping, Sequence
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, urlunsplit

import httpx
from i05_http import ACTIVE_ORG_COOKIE, Session
from i05_report import Finding

AUTH_COOKIES = ("moldy_at", "moldy_rt")
COMPARED_ATTRS = ("secure", "httponly", "samesite", "path", "domain")


def cookie_attrs(response: httpx.Response) -> dict[str, dict[str, str]]:
    """``{cookie name: {attribute: value}}`` from every Set-Cookie header."""
    parsed: dict[str, dict[str, str]] = {}
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            flags = {"secure": "secure" in header.lower(), "httponly": "httponly" in header.lower()}
            attrs = {key: str(morsel[key]).lower() for key in ("samesite", "path", "domain")}
            parsed[name] = {**attrs, **{k: str(v).lower() for k, v in flags.items()}}
    return parsed


def check_cookies(session: Session, https: bool) -> list[Finding]:
    out: list[Finding] = []
    login = cookie_attrs(session.login_response) if session.login_response else {}
    for name in AUTH_COOKIES:
        attrs = login.get(name)
        if attrs is None:
            out.append(Finding("cookie.auth_flags", name, "fail", "not set on login"))
            continue
        problems = [flag for flag in ("httponly",) if attrs.get(flag) != "true"]
        if https and attrs.get("secure") != "true":
            problems.append("secure")
        detail = f"missing {problems}" if problems else ""
        out.append(Finding("cookie.auth_flags", name, "fail" if problems else "pass", detail))
    org = (
        cookie_attrs(session.org_response).get(ACTIVE_ORG_COOKIE) if session.org_response else None
    )
    reference = login.get("moldy_at")
    if org is None or reference is None:
        out.append(Finding("cookie.active_org_attrs", ACTIVE_ORG_COOKIE, "skipped", "not observed"))
    else:
        diff = [key for key in COMPARED_ATTRS if org.get(key) != reference.get(key)]
        verdict = "fail" if diff else "pass"
        detail = f"differs {diff}" if diff else ""
        out.append(Finding("cookie.active_org_attrs", ACTIVE_ORG_COOKIE, verdict, detail))
    return out


def check_headers(response: httpx.Response, https: bool) -> list[Finding]:
    """Advisory hardening headers: missing ones are ``warn``, not failures."""
    target = f"{response.request.method} {response.request.url.path}"
    wanted: dict[str, str] = {"x-content-type-options": "nosniff"}
    if https:
        wanted["strict-transport-security"] = "max-age"
    out = []
    for header, needle in wanted.items():
        value = response.headers.get(header, "")
        verdict = "pass" if needle in value.lower() else "warn"
        out.append(Finding(f"header.{header}", target, verdict, value or "absent"))
    server = response.headers.get("server", "")
    leaks = any(ch.isdigit() for ch in server)
    out.append(Finding("header.server_version", target, "warn" if leaks else "pass", server or "-"))
    return out


async def check_plain_http(base_url: str, transport: httpx.AsyncBaseTransport | None) -> Finding:
    """The http:// twin of an https base must redirect to https or refuse the connection."""
    parts = urlsplit(base_url)
    plain = urlunsplit(("http", parts.hostname or "", parts.path, "", ""))
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10) as client:
            response = await client.get(plain + "/api/health")
    except httpx.TransportError as exc:
        return Finding("tls.plain_http", plain, "pass", f"refused ({type(exc).__name__})")
    location = response.headers.get("location", "")
    ok = response.is_redirect and location.startswith("https://")
    return Finding(
        "tls.plain_http", plain, "pass" if ok else "fail", f"{response.status_code} {location}"
    )


def check_tls(host: str, port: int, timeout: float = 10.0) -> Finding:  # pragma: no cover - network
    """Certificate verifies against the system trust store and TLS is 1.2+."""
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            version = tls.version() or "?"
    except (OSError, ssl.SSLError) as exc:
        return Finding("tls.handshake", f"{host}:{port}", "fail", f"{type(exc).__name__}: {exc}")
    ok = version in ("TLSv1.2", "TLSv1.3")
    return Finding("tls.handshake", f"{host}:{port}", "pass" if ok else "fail", version)


def ids_of(payload: object) -> set[str]:
    """IDs from a list response (bare list or ``items``/``data`` page)."""
    rows = payload
    if isinstance(payload, Mapping):
        rows = payload.get("items") or payload.get("data") or payload.get("results") or []
    if not isinstance(rows, list):
        return set()
    return {str(row["id"]) for row in rows if isinstance(row, Mapping) and "id" in row}


async def rls_parity(
    on: Session, off: Session, paths: Sequence[str]
) -> list[Finding]:  # same persona on the RLS-on and RLS-off deployments
    out: list[Finding] = []
    for path in paths:
        left, right = await on.call("GET", path), await off.call("GET", path)
        if left.status_code != 200 or right.status_code != 200:
            detail = f"status on={left.status_code} off={right.status_code}"
            out.append(Finding("rls.parity", path, "fail", detail))
            continue
        a, b = ids_of(left.json()), ids_of(right.json())
        detail = f"on={len(a)} off={len(b)} only_on={len(a - b)} only_off={len(b - a)}"
        out.append(Finding("rls.parity", path, "pass" if a == b else "fail", detail))
    return out
