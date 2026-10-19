"""E-09 under ``AUTHZ_MODE=enforce``: capability, company tuple and 0 cross-org exposure
before approval even when OpenFGA would allow (plan 10.2, 7.2)."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import AuthzOutbox
from tests.authz.d04_support import ME, OTHER_ORG_ID, AuthzSwitch, authz, seed_scope
from tests.authz.d05_support import BASE, TEST_ORG_ID, TEST_USER_ID, add_item, item_obj
from tests.authz.e09_support import (
    MEMBER,
    OUTSIDER,
    OUTSIDER_ID,
    TENANT,
    acting_as,
    make_admin,
    seed_outsider,
    set_policy,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession

__all__ = ["authz"]
ORG = f"organization:{TEST_ORG_ID}"
COMPANY_TUPLE = f"tenant:{TEST_TENANT_ID}#member installer "


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI, monkeypatch):
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    await seed_scope(db)
    await seed_outsider(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


def _allowed(item_id: UUID, *, cap: bool = True) -> frozenset[tuple[str, str, str]]:
    obj = item_obj(item_id)
    found = {(ME, p, obj) for p in ("can_view", "can_install", "can_manage")}
    found |= {(f"user:{OUTSIDER_ID}", p, obj) for p in ("can_view", "can_install")}
    if cap:
        found.add((ME, "cap_publish_marketplace", ORG))
    return frozenset(found)


async def _company_ops(item_id: UUID) -> list[str]:
    async with TestSession() as db:
        rows = await db.scalars(select(AuthzOutbox).order_by(AuthzOutbox.seq))
        return [
            f"{row.op} {row.tuple_key}"
            for row in rows
            if row.tuple_key.startswith(COMPANY_TUPLE) and str(item_id) in row.tuple_key
        ]


async def _outsider(client: AsyncClient, app: FastAPI, item_id: UUID) -> tuple[int, bool, int]:
    with acting_as(app, OUTSIDER, OTHER_ORG_ID):
        detail = await client.get(f"{BASE}/items/{item_id}")
        listed = {row["id"] for row in (await client.get(f"{BASE}/items")).json()}
        install = await client.post(f"{BASE}/items/{item_id}/install", json={})
    return detail.status_code, str(item_id) in listed, install.status_code


async def test_request_needs_the_publish_capability(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    authz.use(_allowed(item.id, cap=False))
    response = await client.post(f"{BASE}/items/{item.id}/tenant-publication")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


async def test_viewer_without_manage_is_403(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public")
    authz.use(frozenset({(ME, "can_view", item_obj(item.id))}))
    response = await client.post(f"{BASE}/items/{item.id}/tenant-publication")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"


async def test_pending_is_hidden_from_other_orgs_then_approval_writes_the_company_tuple(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    await make_admin(db)
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    authz.use(_allowed(item.id))
    pending = await client.post(f"{BASE}/items/{item.id}/tenant-publication")
    assert pending.status_code == 200, pending.text
    assert pending.json()["tenant_publication"] == "pending"
    # OpenFGA would allow the outsider, but the pending row is not a company candidate.
    assert await _outsider(client, test_app, item.id) == (404, False, 404)
    assert await _company_ops(item.id) == []
    approved = await client.post(f"{TENANT}/publication-requests/{item.id}/approve")
    assert approved.status_code == 200, approved.text
    assert await _company_ops(item.id) == [f"write {COMPANY_TUPLE}{item_obj(item.id)}"]
    detail, listed, _ = await _outsider(client, test_app, item.id)
    assert (detail, listed) == (200, True)
    withdrawn = await client.delete(f"{BASE}/items/{item.id}/tenant-publication")
    assert withdrawn.json()["tenant_publication"] == "none"
    assert (await _company_ops(item.id))[-1] == f"delete {COMPANY_TUPLE}{item_obj(item.id)}"
    assert (await _outsider(client, test_app, item.id))[:2] == (404, False)


async def test_open_policy_writes_the_tuple_at_request(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    await set_policy(db, "open")
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    authz.use(_allowed(item.id))
    response = await client.post(f"{BASE}/items/{item.id}/tenant-publication")
    assert response.json()["tenant_publication"] == "approved"
    assert await _company_ops(item.id) == [f"write {COMPANY_TUPLE}{item_obj(item.id)}"]


async def test_narrowing_visibility_closes_an_approved_item(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    item.tenant_publication = "approved"
    await db.commit()
    authz.use(_allowed(item.id))
    assert (await _outsider(client, test_app, item.id))[:2] == (200, True)
    item.visibility = "private"
    await db.commit()
    assert (await _outsider(client, test_app, item.id))[:2] == (404, False)
