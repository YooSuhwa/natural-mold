"""H-08 hardening: SSRF policy (M4), domain claims (M5), JWKS cache (L4), base URL (L6)."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso import domains as sso_domains
from app.auth.sso import http as sso_http
from app.auth.sso import ldap
from app.config import settings
from app.exceptions import AppError
from app.models.organization import Tenant
from app.models.organization_identity import TenantDomain
from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
from tests.authz.h08_flows import idp_installed, oidc_login, sso_error_of
from tests.authz.h08_support import (
    FakeTxt,
    MockIdp,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.conftest import TestSession

OTHER = uuid4()


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    await make_default_org(db)
    db.add(Tenant(id=OTHER, name="Other", slug="h08-other", secrets_namespace="h08-other"))
    await db.commit()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


@pytest.fixture
def txt() -> Iterator[FakeTxt]:
    fake = FakeTxt()
    previous = sso_domains.txt_resolver()
    sso_domains.set_txt_resolver(fake)
    yield fake
    sso_domains.set_txt_resolver(previous)


@pytest.mark.parametrize(("mode", "guarded"), [("multi_tenant", True), ("single_tenant", False)])
def test_idp_client_uses_the_ssrf_guard_outside_single_company_installs(
    monkeypatch: pytest.MonkeyPatch, mode: str, guarded: bool
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(settings, "deployment_mode", mode)
    monkeypatch.setattr(sso_http, "outbound_http_client", lambda **kw: seen.update(kw))
    sso_http.idp_client()
    assert seen["ssrf_guard"] is guarded and seen["https_only"] is True


async def test_ldap_destination_follows_the_same_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")

    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(ldap, "RESOLVER", private)
    with pytest.raises(AppError) as caught:
        await ldap.checked_addresses("ldaps://dir.corp.example")
    assert caught.value.code == "ldap_unavailable"
    monkeypatch.setattr(settings, "outbound_private_allowed_hosts", "dir.corp.example")
    assert await ldap.checked_addresses("ldaps://dir.corp.example") == ()
    monkeypatch.setattr(settings, "outbound_private_allowed_hosts", "")
    monkeypatch.setattr(ldap, "RESOLVER", public)
    assert await ldap.checked_addresses("ldaps://dir.corp.example:1636") == ("93.184.216.34",)


async def _domains(domain: str) -> list[tuple[UUID, str]]:
    async with TestSession() as db:
        rows = await db.scalars(select(TenantDomain).where(TenantDomain.domain == domain))
        return [(row.tenant_id, row.status) for row in rows]


async def test_competing_pending_claims_and_verification(
    client: AsyncClient, db: AsyncSession, txt: FakeTxt
) -> None:
    await make_admin(db)
    db.add(TenantDomain(tenant_id=OTHER, domain="race.example", txt_token="x"))
    await db.commit()
    added = await client.post(f"{TENANT}/domains", json={"domain": "race.example"})
    assert added.status_code == 201, added.text  # another company's claim is not revealed
    txt.records["race.example"] = [added.json()["txt_record"]]
    assert (await client.post(f"{TENANT}/domains/{added.json()['id']}/verify")).status_code == 200
    assert [status for _, status in await _domains("race.example")] == ["verified"]


async def test_domain_verified_elsewhere_answers_like_a_missing_record(
    client: AsyncClient, db: AsyncSession, txt: FakeTxt
) -> None:
    await make_admin(db)
    db.add(TenantDomain(tenant_id=OTHER, domain="taken.example", txt_token="x", status="verified"))
    await db.commit()
    added = await client.post(f"{TENANT}/domains", json={"domain": "taken.example"})
    assert added.status_code == 201
    txt.records["taken.example"] = [added.json()["txt_record"]]
    refused = await client.post(f"{TENANT}/domains/{added.json()['id']}/verify")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "domain_not_verified"


async def test_jwks_is_cached_and_refetched_for_an_unknown_kid(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await verified_domain(db)
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp()) as idp:
        for _ in range(2):
            assert sso_error_of(await oidc_login(raw_client, idp, connection)) is None
            raw_client.cookies.clear()
        assert idp.fetched.count("/jwks") == 1
        assert idp.fetched.count("/.well-known/openid-configuration") == 1
        idp.kid = "k2"  # the IdP rotated its signing key
        assert sso_error_of(await oidc_login(raw_client, idp, connection)) is None
        assert idp.fetched.count("/jwks") == 2


async def test_redirect_uri_uses_the_configured_public_base_url(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_admin(db)
    await verified_domain(db)
    connection = await seed_connection(db)
    monkeypatch.setattr(settings, "sso_public_base_url", "https://moldy.example.com/")
    view = (await client.get(f"{TENANT}/sso-connections/{connection}")).json()
    assert view["redirect_uri"] == f"https://moldy.example.com/auth/callback/{connection}"
    found = (await client.post("/api/auth/sso/discover", json={"email": "a@corp.example"})).json()
    assert found["connections"][0]["start_url"] == (
        f"https://moldy.example.com/api/auth/sso/{connection}/start"
    )
