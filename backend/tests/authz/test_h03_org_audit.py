"""H-03: ``GET /api/audit-events?scope=org`` and the company audit list (plan 14.1, 9.6, S26)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.e09_support import acting_as
from tests.authz.h03_support import (
    FAR_ORG_ID,
    FAR_TENANT_ID,
    FOREIGN,
    ME,
    OTHER,
    TEST_ORG_ID,
    TEST_TENANT_ID,
    add_event,
    grant_company_role,
    grant_org_role,
    seed_world,
)

pytestmark = pytest.mark.asyncio
ORG = "/api/audit-events?scope=org"
TENANT = f"/api/tenants/{TEST_TENANT_ID}/audit-events"


@pytest.fixture(autouse=True)
async def _world(db: AsyncSession) -> None:
    await seed_world(db)
    await add_event(db, "org.mine", minutes_ago=3)
    await add_event(db, "org.mine.later", minutes_ago=1)
    await add_event(db, "org.sibling", org=OTHER_ORG_ID)
    await add_event(db, "org.unattributed", org=None)
    await add_event(db, "org.far", tenant=FAR_TENANT_ID, org=FAR_ORG_ID)


def _actions(body: dict[str, object]) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    return [item["action"] for item in items]


async def test_org_admin_sees_only_the_current_organization(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, ME.id, "admin")
    with acting_as(test_app, ME, TEST_ORG_ID):
        response = await client.get(ORG)
    assert response.status_code == 200, response.text
    assert _actions(response.json()) == ["org.mine.later", "org.mine"]


async def test_org_auditor_and_company_auditor_may_view(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, OTHER.id, "auditor")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        assert (await client.get(ORG)).status_code == 200
    await grant_company_role(db, ME.id, "auditor")
    with acting_as(test_app, ME, TEST_ORG_ID):
        response = await client.get(ORG)
    assert _actions(response.json()) == ["org.mine.later", "org.mine"]


async def test_plain_member_gets_403_and_keeps_scope_mine(
    client: AsyncClient, test_app: FastAPI
) -> None:
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        denied = await client.get(ORG)
        mine = await client.get("/api/audit-events")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_required"
    assert mine.status_code == 200
    assert set(_actions(mine.json())) >= {"org.mine", "org.sibling"}


async def test_builder_role_is_not_oversight(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, OTHER.id, "builder")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        assert (await client.get(ORG)).status_code == 403


async def test_org_scope_filters_and_cursor_pages(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, ME.id, "admin")
    with acting_as(test_app, ME, TEST_ORG_ID):
        first = (await client.get(f"{ORG}&limit=1")).json()
        second = (await client.get(f"{ORG}&limit=1&cursor={first['next_cursor']}")).json()
        filtered = (await client.get(f"{ORG}&action=org.mine")).json()
    assert (_actions(first), _actions(second)) == (["org.mine.later"], ["org.mine"])
    assert second["next_cursor"] is None
    assert _actions(filtered) == ["org.mine"]


async def test_company_audit_lists_company_rows_with_org_filter(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_company_role(db, ME.id, "admin")
    with acting_as(test_app, ME, TEST_ORG_ID):
        everything = (await client.get(TENANT)).json()
        sibling = (await client.get(f"{TENANT}?org_id={OTHER_ORG_ID}")).json()
        far = await client.get(f"{TENANT}?org_id={FAR_ORG_ID}")
    assert set(_actions(everything)) == {
        "org.mine",
        "org.mine.later",
        "org.sibling",
        "org.unattributed",
    }
    assert _actions(sibling) == ["org.sibling"]
    assert _actions(far.json()) == []  # another company's org never leaks


async def test_company_audit_403_for_members_404_for_outsiders(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, OTHER.id, "admin")  # org admin is not company oversight
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        member = await client.get(TENANT)
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):
        outsider = await client.get(f"/api/tenants/{FAR_TENANT_ID}/audit-events")
    assert member.status_code == 403
    assert outsider.status_code == 404


ORG_ROUTE = f"/api/orgs/{TEST_ORG_ID}/audit-events"


async def test_org_route_admits_non_member_auditors_and_hides_others(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):  # sibling-org member, no role here
        hidden = await client.get(ORG_ROUTE)
    await grant_org_role(db, FOREIGN.id, "auditor")  # granted without membership
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):
        allowed = await client.get(f"{ORG_ROUTE}?action=org.mine")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        member = await client.get(ORG_ROUTE)
    assert hidden.status_code == 404
    assert allowed.status_code == 200 and _actions(allowed.json()) == ["org.mine"]
    assert member.status_code == 403


async def test_org_views_mask_client_details_company_view_keeps_them(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, ME.id, "admin")
    await grant_company_role(db, ME.id, "auditor")
    with acting_as(test_app, ME, TEST_ORG_ID):
        bodies = [(await client.get(url)).json()["items"][0] for url in (ORG, ORG_ROUTE, TENANT)]
    assert [(b["ip_address"], b["user_agent"]) for b in bodies] == [
        (None, None),
        (None, None),
        ("203.0.113.7", "agent/1.0"),
    ]
