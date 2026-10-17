"""H-03: ``GET /api/orgs/{org_id}/usage[/members|/agents]`` (plan 9.6 ``can_view_usage``)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_user import DailySpendUser
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.e01_support import add_agent
from tests.authz.e09_support import acting_as
from tests.authz.h03_support import (
    FOREIGN,
    ME,
    OTHER,
    TEST_ORG_ID,
    TEST_TENANT_ID,
    grant_company_role,
    grant_org_role,
    seed_world,
)

pytestmark = pytest.mark.asyncio
BASE = f"/api/orgs/{TEST_ORG_ID}/usage"
TODAY = date.today()  # noqa: DTZ011 - matches the server-local usage window


def _spend(table: type, org: UUID, cost: str, **target: UUID) -> object:
    return table(
        tenant_id=TEST_TENANT_ID,
        org_id=org,
        date=TODAY,
        total_tokens_in=10,
        total_tokens_out=5,
        total_cost_usd=Decimal(cost),
        request_count=1,
        **target,
    )


@pytest.fixture(autouse=True)
async def world_agent(db: AsyncSession) -> UUID:
    await seed_world(db)
    agent = await add_agent(db)
    db.add_all(
        [
            _spend(DailySpendUser, TEST_ORG_ID, "1.5", user_id=ME.id),
            _spend(DailySpendUser, TEST_ORG_ID, "0.5", user_id=OTHER.id),
            _spend(DailySpendUser, OTHER_ORG_ID, "9", user_id=ME.id),  # same user, other org
            _spend(DailySpendAgent, TEST_ORG_ID, "2", agent_id=agent.id),
            _spend(DailySpendAgent, OTHER_ORG_ID, "7", agent_id=agent.id),
        ]
    )
    await db.commit()
    return agent.id


async def test_org_admin_sees_org_totals_members_and_agents(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, world_agent: UUID
) -> None:
    await grant_org_role(db, OTHER.id, "admin")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        totals = (await client.get(BASE)).json()
        members = (await client.get(f"{BASE}/members")).json()
        agents = (await client.get(f"{BASE}/agents")).json()
    assert (totals["total_cost_usd"], totals["request_count"]) == (2.0, 2)
    assert totals["total_tokens_in"] == 20
    assert [(row["user_id"], row["total_cost_usd"]) for row in members["items"]] == [
        (str(ME.id), 1.5),
        (str(OTHER.id), 0.5),
    ]
    assert members["items"][0]["display_name"] == "Test User"
    assert [(row["agent_id"], row["total_cost_usd"]) for row in agents["items"]] == [
        (str(world_agent), 2.0)
    ]


async def test_window_excludes_older_days(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_org_role(db, OTHER.id, "auditor")
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        response = await client.get(f"{BASE}?from=2000-01-01&to=2000-01-31")
    assert response.status_code == 200 and response.json()["total_cost_usd"] == 0.0


async def test_company_auditor_without_membership_may_view(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession
) -> None:
    await grant_company_role(db, FOREIGN.id, "auditor")  # member of the sibling org only
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):
        response = await client.get(BASE)
    assert response.status_code == 200 and response.json()["total_cost_usd"] == 2.0


async def test_member_403_and_non_member_404(client: AsyncClient, test_app: FastAPI) -> None:
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        member = await client.get(f"{BASE}/members")
    with acting_as(test_app, FOREIGN, OTHER_ORG_ID):
        outsider = await client.get(f"{BASE}/agents")
    assert member.status_code == 403
    assert member.json()["error"]["code"] == "permission_required"
    assert outsider.status_code == 404
