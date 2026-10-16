"""E-09 review fixes: installation updates take the item gate, the approval queue
re-checks publishability, withdrawal needs no capability."""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.mcp_server import McpServer
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
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession

__all__ = ["authz"]
REQUESTS = f"{TENANT}/publication-requests"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI, monkeypatch):
    monkeypatch.setattr("app.database.async_session", TestSession)
    await seed_scope(db)
    await seed_outsider(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _outsider_installation(db: AsyncSession, item: MarketplaceItem) -> UUID:
    version = MarketplaceVersion(
        item_id=item.id,
        version_label="1.0.0",
        version_number=1,
        resource_type="mcp",
        payload_kind="mcp_template",
        payload={},
        content_hash="0" * 64,
    )
    db.add(version)
    await db.flush()
    item.resource_type = "mcp"
    item.latest_version_id = version.id
    server = McpServer(
        user_id=OUTSIDER_ID,
        org_id=OTHER_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name="copy",
        transport="streamable_http",
        url="https://mcp.example.test",
    )
    db.add(server)
    await db.flush()
    installation = MarketplaceInstallation(
        user_id=OUTSIDER_ID,
        org_id=OTHER_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        item_id=item.id,
        version_id=version.id,
        resource_type="mcp",
        installed_mcp_server_id=server.id,
    )
    db.add(installation)
    await db.commit()
    return installation.id


async def test_enforced_installation_update_stops_after_withdrawal(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    item.tenant_publication = "approved"
    installation_id = await _outsider_installation(db, item)
    obj = item_obj(item.id)
    authz.use(frozenset((f"user:{OUTSIDER_ID}", p, obj) for p in ("can_view", "can_install")))
    path = f"{BASE}/installations/{installation_id}/update"
    with acting_as(test_app, OUTSIDER, OTHER_ORG_ID):
        assert (await client.post(path, json={"strategy": "keep_current"})).status_code == 200
    item.tenant_publication = "none"  # withdrawn; OpenFGA still answers allow
    await db.commit()
    with acting_as(test_app, OUTSIDER, OTHER_ORG_ID):
        response = await client.post(path, json={"strategy": "keep_current"})
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"


@pytest.mark.parametrize(
    "change", [{"visibility": "private"}, {"status": "disabled"}, {"status": "draft"}]
)
async def test_queue_and_approve_skip_items_no_longer_publishable(
    client: AsyncClient, db: AsyncSession, change: dict[str, str]
) -> None:
    await make_admin(db)
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    assert (await client.post(f"{BASE}/items/{item.id}/tenant-publication")).status_code == 200
    stored = await db.get(MarketplaceItem, item.id, populate_existing=True)
    assert stored is not None and stored.tenant_publication == "pending"
    for key, value in change.items():
        setattr(stored, key, value)
    await db.commit()
    assert (await client.get(REQUESTS)).json()["items"] == []
    response = await client.post(f"{REQUESTS}/{item.id}/approve")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_withdraw_needs_manage_but_not_the_publish_capability(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    item.tenant_publication = "approved"
    await db.commit()
    obj = item_obj(item.id)
    authz.use(frozenset({(ME, "can_view", obj), (ME, "can_manage", obj)}))  # no cap
    path = f"{BASE}/items/{item.id}/tenant-publication"
    denied = await client.post(path)
    assert denied.json()["error"]["code"] == "capability_required"
    withdrawn = await client.delete(path)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["tenant_publication"] == "none"
    authz.use(frozenset({(ME, "can_view", obj)}))
    assert (await client.delete(path)).status_code == 403  # can_manage still required
    assert (await client.delete(f"{BASE}/items/{uuid4()}/tenant-publication")).status_code == 404
