"""E-09 in legacy mode: the three policies, the approval queue and the visibility rule
"승인 전 다른 조직에 노출 0건" (plan 10.2, 9.7, S05)."""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceItem
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.d05_support import BASE, TEST_ORG_ID, TEST_USER_ID, add_item
from tests.authz.e09_support import (
    MEMBER,
    OUTSIDER,
    TENANT,
    acting_as,
    actions,
    make_admin,
    seed_outsider,
    set_policy,
)
from tests.conftest import TestSession

REQUESTS = f"{TENANT}/publication-requests"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    await seed_outsider(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


def _path(item_id: UUID) -> str:
    return f"{BASE}/items/{item_id}/tenant-publication"


async def _own_item(db: AsyncSession, **kwargs: object) -> MarketplaceItem:
    return await add_item(db, visibility="public", owner=TEST_USER_ID, **kwargs)  # type: ignore[arg-type]


async def _state(item_id: UUID) -> str:
    async with TestSession() as db:
        item = await db.get(MarketplaceItem, item_id)
        assert item is not None
        return item.tenant_publication


async def _outsider_sees(client: AsyncClient, app: FastAPI, item_id: UUID) -> tuple[int, bool]:
    """(detail status, listed) for a member of the company's other organization."""
    with acting_as(app, OUTSIDER, OTHER_ORG_ID):
        detail = await client.get(f"{BASE}/items/{item_id}")
        listed = {row["id"] for row in (await client.get(f"{BASE}/items")).json()}
    return detail.status_code, str(item_id) in listed


async def test_disabled_policy_refuses(client: AsyncClient, db: AsyncSession) -> None:
    await set_policy(db, "disabled")
    item = await _own_item(db)
    response = await client.post(_path(item.id))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_disabled"
    assert await _state(item.id) == "none"


async def test_open_policy_publishes_at_once(client: AsyncClient, db: AsyncSession) -> None:
    await set_policy(db, "open")
    item = await _own_item(db)
    response = await client.post(_path(item.id))
    assert response.status_code == 200, response.text
    assert response.json()["tenant_publication"] == "approved"
    assert await _state(item.id) == "approved"
    events = await actions(item.id)
    assert [e.action for e in events] == ["marketplace.tenant_publish_request"]
    assert events[0].event_metadata is not None
    assert events[0].event_metadata["policy"] == "open"


async def test_default_policy_is_approval_and_hides_from_other_orgs(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    item = await _own_item(db)
    # Legacy reach before any request is unchanged (public items are visible everywhere).
    assert await _outsider_sees(client, test_app, item.id) == (200, True)
    response = await client.post(_path(item.id))
    assert response.json()["tenant_publication"] == "pending"
    assert await _outsider_sees(client, test_app, item.id) == (404, False)
    assert (await client.get(f"{BASE}/items/{item.id}")).status_code == 200  # own org
    again = await client.post(_path(item.id))  # duplicate request: same state
    assert again.status_code == 200 and again.json()["tenant_publication"] == "pending"
    assert len(await actions(item.id)) == 1


async def test_approval_queue_list_and_approve(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    item = await _own_item(db)
    await client.post(_path(item.id))
    member_list = await client.get(REQUESTS)
    assert member_list.status_code == 403
    assert member_list.json()["error"]["code"] == "permission_required"
    assert (await client.post(f"{REQUESTS}/{item.id}/approve")).status_code == 403
    await make_admin(db)
    page = (await client.get(REQUESTS)).json()
    assert [row["item_id"] for row in page["items"]] == [str(item.id)]
    row = page["items"][0]
    assert row["organization"]["id"] == str(TEST_ORG_ID)
    assert row["publisher"]["id"] == str(TEST_USER_ID)
    assert set(row) == {
        "item_id",
        "name",
        "description",
        "resource_type",
        "organization",
        "publisher",
        "requested_at",
        "credential_types",
    }
    approved = await client.post(f"{REQUESTS}/{item.id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["tenant_publication"] == "approved"
    assert (await client.get(REQUESTS)).json()["items"] == []
    assert await _outsider_sees(client, test_app, item.id) == (200, True)
    assert (await client.post(f"{REQUESTS}/{item.id}/approve")).status_code == 404  # not pending
    assert [e.action for e in await actions(item.id)] == [
        "marketplace.tenant_publish_request",
        "marketplace.tenant_publish_approve",
    ]


async def test_reject_needs_a_note_and_keeps_the_item_org_public(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await make_admin(db)
    item = await _own_item(db)
    await client.post(_path(item.id))
    assert (await client.post(f"{REQUESTS}/{item.id}/reject", json={})).status_code == 422
    assert (
        await client.post(f"{REQUESTS}/{item.id}/reject", json={"note": " "})
    ).status_code == 422
    rejected = await client.post(f"{REQUESTS}/{item.id}/reject", json={"note": "보안 검토 필요"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["tenant_publication"] == "rejected"
    assert rejected.json()["note"] == "보안 검토 필요"
    assert await _outsider_sees(client, test_app, item.id) == (404, False)
    assert (await client.get(f"{BASE}/items/{item.id}")).status_code == 200
    events = await actions(item.id)
    assert events[-1].action == "marketplace.tenant_publish_reject"
    assert events[-1].reason_message == "보안 검토 필요"
    retry = await client.post(_path(item.id))  # a rejected item may ask again
    assert retry.json()["tenant_publication"] == "pending"
    assert retry.json()["note"] is None


async def test_withdraw_returns_to_none_with_audit(client: AsyncClient, db: AsyncSession) -> None:
    item = await _own_item(db)
    await client.post(_path(item.id))
    response = await client.delete(_path(item.id))
    assert response.status_code == 200
    assert response.json()["tenant_publication"] == "none"
    assert (await client.delete(_path(item.id))).status_code == 200  # idempotent
    assert [e.action for e in await actions(item.id)] == [
        "marketplace.tenant_publish_request",
        "marketplace.tenant_publish_cancel",
    ]


async def test_only_published_public_org_items_and_only_managers(
    client: AsyncClient, db: AsyncSession
) -> None:
    for kwargs in ({"visibility": "private"}, {"status": "draft"}, {"visibility": "unlisted"}):
        item = await add_item(db, owner=TEST_USER_ID, **{"visibility": "public", **kwargs})  # type: ignore[arg-type]
        response = await client.post(_path(item.id))
        assert response.status_code == 409, kwargs
        assert response.json()["error"]["code"] == "tenant_publication_invalid"
    foreign = await add_item(db, visibility="public")  # another member's item (legacy: owner)
    assert (await client.post(_path(foreign.id))).status_code == 404
    assert (await client.post(_path(uuid4()))).status_code == 404
    assert await _state(foreign.id) == "none"


async def test_admin_of_the_company_decides_only_its_own_requests(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    assert (await client.post(f"{REQUESTS}/{uuid4()}/approve")).status_code == 404
    response = await client.get(f"/api/tenants/{uuid4()}/publication-requests")
    assert response.status_code == 404
