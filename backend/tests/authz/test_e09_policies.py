"""E-09: ``GET|PUT /api/tenants/{tenant_id}/policies`` (plan 9.7, S05)."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

POLICIES = f"{TENANT}/policies"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def test_member_reads_the_default_approval_policy(client: AsyncClient) -> None:
    response = await client.get(POLICIES)
    assert response.status_code == 200, response.text
    assert response.json()["marketplace_tenant_publish"] == "approval"
    assert response.headers["ETag"]


async def test_foreign_company_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/tenants/{uuid4()}/policies")
    assert response.status_code == 404


async def test_member_cannot_update(client: AsyncClient) -> None:
    etag = (await client.get(POLICIES)).headers["ETag"]
    response = await client.put(
        POLICIES, json={"marketplace_tenant_publish": "open"}, headers={"If-Match": etag}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"


async def test_admin_updates_with_audit_and_version(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    etag = (await client.get(POLICIES)).headers["ETag"]
    stale = await client.put(
        POLICIES, json={"marketplace_tenant_publish": "open"}, headers={"If-Match": '"x"'}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"
    response = await client.put(
        POLICIES, json={"marketplace_tenant_publish": "disabled"}, headers={"If-Match": etag}
    )
    assert response.status_code == 200, response.text
    assert response.json()["marketplace_tenant_publish"] == "disabled"
    assert response.headers["ETag"] != etag
    assert (await client.get(POLICIES)).json()["marketplace_tenant_publish"] == "disabled"
    events = [e for e in await actions(TEST_TENANT_ID) if e.action.startswith("policy.")]
    assert [e.action for e in events] == ["policy.marketplace_publish_update"]
    assert events[0].event_metadata is not None
    assert (events[0].event_metadata["before"], events[0].event_metadata["after"]) == (
        "approval",
        "disabled",
    )


async def test_invalid_value_is_422(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    etag = (await client.get(POLICIES)).headers["ETag"]
    response = await client.put(
        POLICIES, json={"marketplace_tenant_publish": "anyone"}, headers={"If-Match": etag}
    )
    assert response.status_code == 422
