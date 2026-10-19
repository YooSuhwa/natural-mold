"""H-08 request flows shared by the OIDC/LDAP tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import httpx
from sqlalchemy import select

from app.auth.sso import http as sso_http
from app.auth.sso import oidc
from app.models.audit_event import AuditEvent
from tests.authz.h08_support import ISSUER, MockIdp
from tests.conftest import TestSession


@contextmanager
def idp_installed(idp: MockIdp) -> Iterator[MockIdp]:
    previous = sso_http.TRANSPORT
    sso_http.TRANSPORT = idp.transport()
    oidc.clear_cache()
    try:
        yield idp
    finally:
        sso_http.TRANSPORT = previous
        oidc.clear_cache()


async def oidc_login(
    client: httpx.AsyncClient, idp: MockIdp, connection_id: UUID, *, state: str | None = None
) -> httpx.Response:
    start = await client.get(f"/api/auth/sso/{connection_id}/start")
    assert start.status_code == 302, start.text
    location = start.headers["location"]
    assert location.startswith(f"{ISSUER}/authorize?"), location
    code, issued_state = idp.authorize(location)
    return await client.get(
        f"/auth/callback/{connection_id}", params={"code": code, "state": state or issued_state}
    )


def sso_error_of(response: httpx.Response) -> str | None:
    location = response.headers.get("location", "")
    marker = "sso_error="
    return location.split(marker, 1)[1] if marker in location else None


async def audit_actions(action: str) -> list[AuditEvent]:
    async with TestSession() as db:
        rows = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
        return list(rows)
