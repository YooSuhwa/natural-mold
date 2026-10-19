"""H-02: notification API scope, cursor, read/delete and the read-time re-check (14.2)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target
from app.models.organization import TenantRoleGrant
from tests.authz.d04_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    AuthzSwitch,
    Unavailable,
    add_tool,
    allow,
    authz,
)
from tests.authz.e01_support import seed
from tests.authz.h02_support import PATH, backdate, join_other_org, push, rows
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def test_empty_bell(client: AsyncClient) -> None:
    page = await client.get(PATH)
    assert page.status_code == 200
    assert page.json() == {"items": [], "next_cursor": None, "has_more": False}
    count = await client.get(f"{PATH}/unread-count")
    assert count.json() == {"total": 0, "by_org": {}}


async def test_only_my_rows_of_the_current_org_are_listed_and_counted(
    client: AsyncClient, db: AsyncSession
) -> None:
    await join_other_org(db)
    mine = await push(db, actor=OTHER_USER_ID)
    await push(db, org=OTHER_ORG_ID)
    await push(db, org=OTHER_ORG_ID, kind="trigger.blocked")
    stranger = await push(db, user=OTHER_USER_ID)
    company = await push(db, org=None, kind="tenant.suspended", link=None)
    items = (await client.get(PATH)).json()["items"]
    assert [item["id"] for item in items] == [str(mine.id)]
    assert items[0]["actor"] == {"id": str(OTHER_USER_ID), "display_name": "Other"}
    count = (await client.get(f"{PATH}/unread-count")).json()
    assert count == {"total": 1, "by_org": {str(TEST_ORG_ID): 1, str(OTHER_ORG_ID): 2}}
    for row in (stranger, company):  # another user's row; company row for a non-admin
        assert (await client.post(f"{PATH}/{row.id}/read")).status_code == 404
        assert (await client.delete(f"{PATH}/{row.id}")).status_code == 404


async def test_company_rows_are_shown_to_company_admins(
    client: AsyncClient, db: AsyncSession
) -> None:
    db.add(TenantRoleGrant(tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID, role="admin"))
    await db.commit()
    company = await push(db, org=None, kind="tenant.suspended", link=None, reason="billing")
    [item] = (await client.get(PATH)).json()["items"]
    assert (item["id"], item["org_id"], item["payload"]) == (
        str(company.id),
        None,
        {"reason": "billing"},
    )


async def test_cursor_pages_newest_first(client: AsyncClient, db: AsyncSession) -> None:
    first = await push(db, kind="grant.received")
    second = await push(db, kind="trigger.blocked")
    await backdate(first.id, datetime.now(UTC) - timedelta(minutes=5))
    page = (await client.get(PATH, params={"limit": 1})).json()
    assert [i["id"] for i in page["items"]] == [str(second.id)] and page["has_more"]
    rest = (await client.get(PATH, params={"limit": 1, "cursor": page["next_cursor"]})).json()
    assert [i["id"] for i in rest["items"]] == [str(first.id)] and not rest["has_more"]
    assert (await client.get(PATH, params={"cursor": "%%%"})).status_code == 422


async def test_read_one_read_all_unread_filter_and_delete(
    client: AsyncClient, db: AsyncSession
) -> None:
    await join_other_org(db)
    one = await push(db, kind="grant.received")
    two = await push(db, kind="trigger.blocked")
    elsewhere = await push(db, org=OTHER_ORG_ID)
    assert (await client.post(f"{PATH}/{one.id}/read")).status_code == 204
    unread = (await client.get(PATH, params={"unread": True})).json()["items"]
    assert [i["id"] for i in unread] == [str(two.id)]
    assert (await client.post(f"{PATH}/read-all")).json() == {"updated": 1}
    assert (await client.get(f"{PATH}/unread-count")).json()["by_org"] == {
        str(OTHER_ORG_ID): 1  # read-all stays in the current organization
    }
    assert (await client.delete(f"{PATH}/{two.id}")).status_code == 204
    assert {row.id for row in await rows(TEST_USER_ID)} == {one.id, elsewhere.id}


async def test_targets_the_recipient_can_no_longer_see_are_masked(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID, name="영업 리포트")
    await push(db, target=Target("tool", tool.id, tool.name), actor=OTHER_USER_ID, level="viewer")
    authz.use()  # enforce, no tuples: the tool is hidden from the recipient
    [hidden] = (await client.get(PATH)).json()["items"]
    assert hidden["target"] == {"type": "tool", "id": None, "name": None, "accessible": False}
    assert hidden["actor"] is None  # who shared it is withheld too
    assert (hidden["link"], hidden["payload"]) == (None, {"level": "viewer"})
    assert "영업" not in str(hidden)
    authz.use(allow(("can_use", f"tool:{tool.id}")))
    [shown] = (await client.get(PATH)).json()["items"]
    assert shown["target"] == {
        "type": "tool",
        "id": str(tool.id),
        "name": "영업 리포트",
        "accessible": True,
    }
    assert shown["link"] == "/tools"
    assert shown["actor"]["id"] == str(OTHER_USER_ID)
    authz.use(client=Unavailable())  # outage: masked, not a failing bell
    response = await client.get(PATH)
    assert response.status_code == 200
    assert response.json()["items"][0]["target"]["accessible"] is False


async def test_legacy_shows_own_targets_only(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    mine, theirs = await add_tool(db, name="mine"), await add_tool(db, owner=OTHER_USER_ID)
    await push(db, target=Target("tool", mine.id, "mine"))
    await push(db, kind="trigger.blocked", target=Target("tool", theirs.id, "theirs"))
    authz.use(mode="legacy")
    items = (await client.get(PATH)).json()["items"]
    assert {i["target"]["name"] for i in items} == {"mine", None}
