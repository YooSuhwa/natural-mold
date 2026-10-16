"""E-07 ``/api/tenants/{id}/credentials``: company shared credentials (6.2, 7.2, S07)."""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e07_support import TENANT, credential, enforced, foreign_tenant, stored
from tests.authz.e09_support import MEMBER, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

CREDENTIALS = f"{TENANT}/credentials"
SECRET = "sk-company-secret-value"
NEW = {"definition_key": "openai", "name": "사내 키", "data": {"api_key": SECRET}}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def test_member_is_refused(client: AsyncClient) -> None:
    response = await client.get(CREDENTIALS)
    assert response.status_code == 403
    assert (await client.post(CREDENTIALS, json=NEW)).status_code == 403


async def test_admin_creates_a_company_credential_without_echoing_values(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    created = await client.post(CREDENTIALS, json={**NEW, "allowed_org_ids": [str(TEST_ORG_ID)]})
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    body = created.json()
    assert (body["field_keys"], body["allowed_org_ids"]) == (["api_key"], [str(TEST_ORG_ID)])
    row = await db.get(Credential, UUID(body["id"]))
    assert row is not None
    assert (row.scope, row.tenant_id, row.org_id, row.user_id, row.is_system) == (
        "tenant",
        TEST_TENANT_ID,
        None,
        None,
        True,
    )
    listed = (await client.get(CREDENTIALS)).json()
    assert [item["id"] for item in listed] == [body["id"]] and SECRET not in str(listed)
    # Never a platform credential nor a member's credential.
    assert all(c.id != row.id for c in await credential_service.list_system(db))
    assert await credential_service.get_system(db, row.id) is None
    assert await credential_service.find_system_by_definition(db, "openai") is None
    assert "openai" not in await credential_service.get_provider_keys(db)
    assert "tenant.credential_create" in [e.action for e in await actions(body["id"])]


async def test_update_designation_and_delete(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    body = (await client.post(CREDENTIALS, json=NEW)).json()
    assert body["allowed_org_ids"] == []
    path = f"{CREDENTIALS}/{body['id']}"
    changed = await client.patch(
        path,
        json={
            "name": "교체",
            "data": {"api_key": "sk-new"},
            "allowed_org_ids": [str(OTHER_ORG_ID)],
        },
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["name"], changed.json()["allowed_org_ids"]) == (
        "교체",
        [str(OTHER_ORG_ID)],
    )
    assert (await client.delete(path)).status_code == 204
    assert await db.get(Credential, UUID(body["id"]), populate_existing=True) is None
    assert (await client.patch(path, json={"name": "x"})).status_code == 404


async def test_foreign_credentials_orgs_and_definitions_are_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    foreign = await credential(db, tenant_id=await foreign_tenant(db), key="sk-foreign")
    platform = await credential(db, tenant_id=None)
    for target in (foreign.id, platform.id):
        assert (
            await client.patch(f"{CREDENTIALS}/{target}", json={"name": "x"})
        ).status_code == 404
        assert (await client.delete(f"{CREDENTIALS}/{target}")).status_code == 404
    stranger = await client.post(CREDENTIALS, json={**NEW, "allowed_org_ids": [str(uuid4())]})
    assert stranger.status_code == 404
    unknown = await client.post(CREDENTIALS, json={**NEW, "definition_key": "nope"})
    assert unknown.status_code == 422


async def test_enforced_designation_grants_and_revokes(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_admin(db)
    with enforced(test_app, monkeypatch) as store:
        body = (
            await client.post(CREDENTIALS, json={**NEW, "allowed_org_ids": [str(TEST_ORG_ID)]})
        ).json()
        obj = f"credential:{body['id']}"
        assert (f"tenant:{TEST_TENANT_ID}", "tenant", obj) in stored(store)
        assert (f"organization:{TEST_ORG_ID}", "allowed_org", obj) in stored(store)
        assert (f"organization:{TEST_ORG_ID}#member", "consumer", obj) in stored(store)
        cleared = await client.patch(f"{CREDENTIALS}/{body['id']}", json={"allowed_org_ids": []})
        assert cleared.status_code == 200, cleared.text
        assert not any(t[2] == obj and t[1] in ("allowed_org", "consumer") for t in stored(store))
        assert (await client.delete(f"{CREDENTIALS}/{body['id']}")).status_code == 204
        assert not any(t[2] == obj for t in stored(store))
