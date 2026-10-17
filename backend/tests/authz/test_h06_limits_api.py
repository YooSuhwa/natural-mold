"""H-06 limit APIs: who sets which tier, narrowing 422, views (plan 8.7, 9.7, S08·S10·S26)."""

from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.e01_support import add_agent
from tests.authz.e09_support import acting_as
from tests.authz.h03_support import (
    FAR_TENANT_ID,
    FOREIGN,
    ME,
    OTHER,
    TEST_ORG_ID,
    TEST_TENANT_ID,
    TEST_USER_ID,
    grant_company_role,
    grant_org_role,
    seed_world,
)
from tests.authz.h06_support import (
    fresh_limits,
    set_member_limit,
    set_org_limit,
    set_tenant_limits,
    spend,
)

__all__ = ["fresh_limits"]
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_limits")]
URL = "/api/usage-limits"


@pytest.fixture
async def agent_id(db: AsyncSession) -> UUID:
    await seed_world(db)
    return (await add_agent(db)).id


def _query(scope: str, scope_id: UUID, **extra: str) -> dict[str, str]:
    return {"scope": scope, "id": str(scope_id), **extra}


async def _put(client: AsyncClient, scope: str, scope_id: UUID, body: dict) -> Response:
    """PUT with the ETag of a fresh GET (none when the caller may not read the limit)."""
    params = _query(scope, scope_id)
    current = await client.get(URL, params=params)
    headers = {"If-Match": current.headers["ETag"]} if current.status_code == 200 else {}
    return await client.put(URL, params=params, json=body, headers=headers)


async def _put_org(client: AsyncClient, base: str, body: dict) -> Response:
    current = await client.get(base)
    headers = {"If-Match": current.headers["ETag"]} if current.status_code == 200 else {}
    return await client.put(base, json=body, headers=headers)


def _parent_error(response: Response, scope: str, parent_scope: str, parent: float) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "limit_exceeds_parent"
    assert error["details"] == {
        "scope": scope,
        "parent_scope": parent_scope,
        "parent_limit": parent,
    }


async def test_company_admin_sets_org_limit_within_the_company_limit(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, agent_id: UUID
) -> None:
    await grant_company_role(db, OTHER.id, "admin")
    await set_tenant_limits(db, monthly_spend_limit="100")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        above = await _put(client, "org", TEST_ORG_ID, {"monthly_spend_limit": "150"})
        saved = await _put(client, "org", TEST_ORG_ID, {"monthly_spend_limit": "60"})
        sibling = await _put(client, "org", OTHER_ORG_ID, {"monthly_spend_limit": "70"})
        company = (await client.get(URL, params=_query("tenant", TEST_TENANT_ID))).json()
    _parent_error(above, "org", "tenant", 100.0)
    assert saved.status_code == 200, saved.text
    assert Decimal(saved.json()["monthly_spend_limit"]) == 60
    assert Decimal(saved.json()["parent_limit"]) == 100
    assert sibling.status_code == 200
    assert Decimal(company["allocated"]) == 130  # 60 + 70 may exceed nothing, shown as allocated
    org = await db.get(Organization, TEST_ORG_ID, populate_existing=True)
    assert org is not None and org.monthly_spend_limit == Decimal(60)


async def test_org_admin_sets_user_and_agent_limits_under_the_tightest_parent(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, agent_id: UUID
) -> None:
    await grant_org_role(db, OTHER.id, "admin")
    await set_tenant_limits(db, monthly_spend_limit="50")
    await set_org_limit(db, "80")  # company is tighter
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        user_above = await _put(client, "user", TEST_USER_ID, {"monthly_spend_limit": "60"})
        user_ok = await _put(client, "user", TEST_USER_ID, {"monthly_spend_limit": "20"})
        agent_above = await _put(client, "agent", agent_id, {"monthly_spend_limit": "51"})
        agent_ok = await _put(client, "agent", agent_id, {"monthly_spend_limit": "50"})
        company_only = await _put(client, "user", TEST_USER_ID, {"member_limit": 3})
    _parent_error(user_above, "user", "tenant", 50.0)
    _parent_error(agent_above, "agent", "tenant", 50.0)
    assert user_ok.status_code == 200 and Decimal(user_ok.json()["effective_limit"]) == 20
    assert agent_ok.status_code == 200
    assert company_only.status_code == 422
    agent = await db.get(Agent, agent_id, populate_existing=True)
    assert agent is not None and agent.monthly_spend_limit == Decimal(50)


async def test_who_may_set_each_tier(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, agent_id: UUID
) -> None:
    body = {"monthly_spend_limit": "5"}
    with acting_as(test_app, OTHER, TEST_ORG_ID):  # plain member
        member_user = await _put(client, "user", TEST_USER_ID, body)
        member_agent = await _put(client, "agent", agent_id, body)
        member_org = await _put(client, "org", TEST_ORG_ID, body)
        member_tenant = await _put(client, "tenant", TEST_TENANT_ID, body)
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):  # another organization only
        outsider = await client.put(
            URL, params=_query("user", TEST_USER_ID, org_id=str(TEST_ORG_ID)), json=body
        )
        foreign_tenant = await client.get(URL, params=_query("tenant", FAR_TENANT_ID))
    for response in (member_user, member_agent, member_org, member_tenant):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "permission_required"
    assert outsider.status_code == 404
    assert foreign_tenant.status_code == 404


async def test_company_limits_are_set_by_the_company_admin_only_when_installed(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_world(db)
    await grant_company_role(db, OTHER.id, "admin")
    body = {"monthly_spend_limit": "500", "member_limit": 10, "request_rate_limit": 30}
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        saved = await _put(client, "tenant", TEST_TENANT_ID, body)
        monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
        saas = await _put(client, "tenant", TEST_TENANT_ID, {"member_limit": 99})
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert (view["member_limit"], view["members"], view["request_rate_limit"]) == (10, 3, 30)
    assert saas.status_code == 403
    tenant = await db.get(Tenant, TEST_TENANT_ID, populate_existing=True)
    assert tenant is not None and tenant.limits["member_limit"] == 10


async def test_member_default_limit_on_the_org_page(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await seed_world(db)
    await grant_org_role(db, OTHER.id, "admin")
    await set_org_limit(db, "40")
    base = f"/api/orgs/{TEST_ORG_ID}/limits"
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        above = await _put_org(client, base, {"default_member_limit": "41"})
        saved = await _put_org(client, base, {"default_member_limit": "10"})
        user_view = (await client.get(URL, params=_query("user", TEST_USER_ID))).json()
    with acting_as(test_app, ME, TEST_ORG_ID):  # member without can_manage_policy
        denied = await _put_org(client, base, {"default_member_limit": "1"})
    _parent_error(above, "user", "org", 40.0)
    assert saved.status_code == 200 and Decimal(saved.json()["default_member_limit"]) == 10
    assert user_view["monthly_spend_limit"] is None
    assert Decimal(user_view["effective_limit"]) == 10
    assert denied.status_code == 403


async def test_my_limit_shows_remaining_under_every_higher_limit(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await seed_world(db)
    await set_member_limit(db, OTHER.id, "30")
    await set_org_limit(db, "12")
    await spend(db, "4", user_id=OTHER.id)
    await spend(db, "5", user_id=TEST_USER_ID)
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        mine = (await client.get("/api/me/usage-limits")).json()
        own = await client.get(URL, params=_query("user", OTHER.id))
        others = await client.get(URL, params=_query("user", TEST_USER_ID))
    assert (Decimal(mine["limit"]), Decimal(mine["used"])) == (30, 4)
    assert Decimal(mine["remaining"]) == 3  # the organization has 12 - 9 left
    assert mine["blocked"] is False and mine["resets_at"]
    assert own.status_code == 200 and others.status_code == 403
    assert own.json()["parent_limit"] is None  # no can_view_usage: no higher amounts
    assert Decimal(own.json()["effective_limit"]) == 30


async def test_company_usage_and_limits_views(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await seed_world(db)
    await grant_company_role(db, OTHER.id, "auditor")
    await set_tenant_limits(db, monthly_spend_limit="20", concurrent_run_limit=4)
    await spend(db, "3")
    await spend(db, "2", org_id=OTHER_ORG_ID)
    base = f"/api/tenants/{TEST_TENANT_ID}"
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        usage = (await client.get(f"{base}/usage")).json()
        company = (await client.get(f"{base}/limits")).json()
    with acting_as(test_app, ME, TEST_ORG_ID):
        member = await client.get(f"{base}/usage")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        far = await client.get(f"/api/tenants/{FAR_TENANT_ID}/limits")
    assert usage["total_cost_usd"] == 5.0
    assert [(row["org_id"], row["total_cost_usd"]) for row in usage["organizations"]][:2] == [
        (str(TEST_ORG_ID), 3.0),
        (str(OTHER_ORG_ID), 2.0),
    ]
    assert (Decimal(company["used"]), Decimal(company["remaining"])) == (5, 15)
    assert (company["concurrent_run_limit"], company["active_runs"]) == (4, 0)
    assert member.status_code == 403 and far.status_code == 404


async def test_stale_or_missing_if_match_is_409_and_saves_are_audited(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, agent_id: UUID
) -> None:
    await grant_org_role(db, OTHER.id, "admin")
    params = _query("agent", agent_id)
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        etag = (await client.get(URL, params=params)).headers["ETag"]
        missing = await client.put(URL, params=params, json={"monthly_spend_limit": "4"})
        saved = await client.put(
            URL, params=params, json={"monthly_spend_limit": "4"}, headers={"If-Match": etag}
        )
        stale = await client.put(
            URL, params=params, json={"monthly_spend_limit": "3"}, headers={"If-Match": etag}
        )
    assert missing.status_code == 409 and stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"
    assert saved.status_code == 200 and saved.headers["ETag"] != etag
    events = list(
        await db.scalars(select(AuditEvent).where(AuditEvent.action == "policy.limits_update"))
    )
    assert len(events) == 1
    event = events[0]
    assert (event.target_type, event.target_id, event.actor_user_id) == (
        "agent",
        str(agent_id),
        OTHER.id,
    )
    assert (event.event_metadata or {})["scope"] == "agent"
    assert (event.event_metadata or {})["before"] == {"monthly_spend_limit": None}
    assert Decimal((event.event_metadata or {})["after"]["monthly_spend_limit"]) == 4
