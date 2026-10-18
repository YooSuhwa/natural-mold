"""Authenticated Moldy API sessions for the I-05 harnesses (cookie + CSRF double submit)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from i05_safety import Persona

MUTATIONS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
ACTIVE_ORG_COOKIE = "moldy_active_org"


class UnexpectedStatus(RuntimeError):
    """A scenario step got a status it did not expect (counted as an error)."""

    def __init__(self, method: str, path: str, status: int, expected: set[int]) -> None:
        super().__init__(f"{method} {path} -> {status} (expected {sorted(expected)})")
        self.status = status


def expect(response: httpx.Response, *expected: int) -> httpx.Response:
    allowed = set(expected) or {200}
    if response.status_code not in allowed:
        request = response.request
        raise UnexpectedStatus(request.method, request.url.path, response.status_code, allowed)
    return response


def error_code(response: httpx.Response) -> str | None:
    """The API error ``code`` (``{"code": ...}`` or ``{"detail": {"code": ...}}``)."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for candidate in (body, body.get("detail"), body.get("error")):
        if isinstance(candidate, dict) and isinstance(candidate.get("code"), str):
            return candidate["code"]
    return None


@dataclass
class Session:
    """One logged-in persona; mutations carry the CSRF header unless told not to."""

    persona: str
    client: httpx.AsyncClient
    csrf: str
    user_id: str
    org_id: str | None
    login_response: httpx.Response | None = None
    org_response: httpx.Response | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    async def call(
        self, method: str, path: str, *, csrf: bool = True, **kwargs: Any
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        if csrf and method.upper() in MUTATIONS:
            headers.setdefault(CSRF_HEADER, self.csrf)
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def aclose(self) -> None:
        await self.client.aclose()


def make_client(base_url: str, timeout_s: float, **kwargs: Any) -> httpx.AsyncClient:
    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0))
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    return httpx.AsyncClient(
        base_url=base_url, timeout=timeout, limits=limits, follow_redirects=False, **kwargs
    )


async def login(
    base_url: str,
    persona: Persona,
    *,
    timeout_s: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Session:
    """Log in with the env-provided password and select the persona's organization."""
    client = make_client(base_url, timeout_s, transport=transport)
    try:
        response = await client.post(
            "/api/auth/login", json={"email": persona.email, "password": persona.password()}
        )
        expect(response, 200)
        body = response.json()
        session = Session(
            persona=persona.name,
            client=client,
            csrf=str(body["csrf_token"]),
            user_id=str(body["user"]["id"]),
            org_id=persona.org_id,
            login_response=response,
        )
        if persona.org_id:
            org = await session.call("POST", "/api/me/active-org", json={"org_id": persona.org_id})
            session.org_response = expect(org, 200)
        return session
    except BaseException:
        await client.aclose()
        raise


async def login_all(
    base_url: str,
    personas: list[Persona],
    *,
    timeout_s: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Session]:
    sessions: dict[str, Session] = {}
    try:
        for persona in personas:
            sessions[persona.name] = await login(
                base_url, persona, timeout_s=timeout_s, transport=transport
            )
    except BaseException:
        for session in sessions.values():
            await session.aclose()
        raise
    return sessions
