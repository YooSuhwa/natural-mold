"""H-08: company domain verification by DNS TXT (plan 9.7, 10.10; S30)."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso import domains as sso_domains
from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
from tests.authz.h08_flows import audit_actions
from tests.authz.h08_support import FakeTxt

DOMAINS = f"{TENANT}/domains"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


@pytest.fixture
def txt() -> Iterator[FakeTxt]:
    fake = FakeTxt()
    previous = sso_domains.txt_resolver()
    sso_domains.set_txt_resolver(fake)
    yield fake
    sso_domains.set_txt_resolver(previous)


async def test_member_cannot_read_or_add_domains(client: AsyncClient) -> None:
    assert (await client.get(DOMAINS)).status_code == 403
    response = await client.post(DOMAINS, json={"domain": "corp.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"


async def test_foreign_company_is_404(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    assert (await client.get(f"/api/tenants/{uuid4()}/domains")).status_code == 404


async def test_add_verify_and_remove_a_domain(
    client: AsyncClient, db: AsyncSession, txt: FakeTxt
) -> None:
    await make_admin(db)
    added = await client.post(DOMAINS, json={"domain": "  Corp.Example. "})
    assert added.status_code == 201, added.text
    body = added.json()
    assert (body["domain"], body["status"]) == ("corp.example", "pending")
    assert body["txt_record"].startswith("moldy-verify=")
    verify = f"{DOMAINS}/{body['id']}/verify"
    missing = await client.post(verify)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "domain_not_verified"
    txt.records["corp.example"] = ["v=spf1 -all", body["txt_record"]]
    verified = await client.post(verify)
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"
    assert verified.json()["verified_at"] is not None
    listed = (await client.get(DOMAINS)).json()["items"]
    assert [item["domain"] for item in listed] == ["corp.example"]
    assert (await client.delete(f"{DOMAINS}/{body['id']}")).status_code == 204
    assert (await client.get(DOMAINS)).json()["items"] == []
    actions = [e.action for e in await audit_actions("tenant.sso_domain_verify")]
    assert actions == ["tenant.sso_domain_verify"]


async def test_wrong_token_does_not_verify(
    client: AsyncClient, db: AsyncSession, txt: FakeTxt
) -> None:
    await make_admin(db)
    body = (await client.post(DOMAINS, json={"domain": "corp.example"})).json()
    txt.records["corp.example"] = ["moldy-verify=someone-else"]
    assert (await client.post(f"{DOMAINS}/{body['id']}/verify")).status_code == 409


@pytest.mark.parametrize(
    "domain", ["localhost", "*.corp.example", "corp.example/x", "-bad.example", "1.2.3.4"]
)
async def test_invalid_domains_are_422(client: AsyncClient, db: AsyncSession, domain: str) -> None:
    await make_admin(db)
    response = await client.post(DOMAINS, json={"domain": domain})
    assert response.status_code == 422


async def test_a_domain_belongs_to_one_company(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    assert (await client.post(DOMAINS, json={"domain": "corp.example"})).status_code == 201
    again = await client.post(DOMAINS, json={"domain": "CORP.example"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "domain_taken"
