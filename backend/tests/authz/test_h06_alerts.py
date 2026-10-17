"""H-06 ``usage.limit_warning`` at 80% and 100% (plan 8.7 "알림과 차단", 14.2)."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.notification import Notification
from app.tenancy import limit_alerts, limits
from app.tenancy.limits import admit_run
from tests.authz.e01_support import add_agent
from tests.authz.e09_support import acting_as
from tests.authz.h03_support import (
    OTHER,
    TEST_ORG_ID,
    TEST_USER_ID,
    grant_company_role,
    grant_org_role,
    seed_world,
)
from tests.authz.h06_support import (
    attribution,
    fresh_limits,
    set_agent_limit,
    set_member_limit,
    set_tenant_limits,
    spend,
)
from tests.conftest import TestSession

__all__ = ["fresh_limits"]
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_limits")]


@pytest.fixture
async def agent(db: AsyncSession) -> UUID:
    await seed_world(db)
    return (await add_agent(db)).id


async def _warnings() -> list[tuple[UUID, UUID | None, dict[str, object]]]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(Notification)
            .where(Notification.kind == "usage.limit_warning")
            .order_by(Notification.created_at)
        )
        return [(row.user_id, row.org_id, dict(row.payload)) for row in rows]


async def _admit(db: AsyncSession, agent: UUID) -> None:
    try:
        await admit_run(db, attribution(agent), enforce=True)
    except AppError as error:
        assert error.code == "quota_exceeded"
    await db.rollback()


async def test_company_limit_warns_company_admins_once_per_threshold(
    db: AsyncSession, agent: UUID
) -> None:
    await grant_company_role(db, OTHER.id, "admin")
    await set_tenant_limits(db, monthly_spend_limit="10")
    await spend(db, "7.9", agent_id=agent)
    await _admit(db, agent)
    assert await _warnings() == []

    await spend(db, "0.1", agent_id=agent)  # 8 of 10 = 80%
    await _admit(db, agent)
    await _admit(db, agent)
    limit_alerts.reset_sent()  # another process: the stored row still dedupes
    limits.reset_cache()
    await _admit(db, agent)
    warnings = await _warnings()
    assert [(user, org, payload["threshold"]) for user, org, payload in warnings] == [
        (OTHER.id, None, 80)
    ]
    assert warnings[0][2]["scope"] == "tenant" and warnings[0][2]["limit"] == "10"

    await spend(db, "2", agent_id=agent)  # 100%: blocked and warned
    await _admit(db, agent)
    assert [payload["threshold"] for _, _, payload in await _warnings()] == [80, 100]


async def test_user_limit_warns_the_user_and_org_admins_agent_limit_only_admins(
    db: AsyncSession, agent: UUID
) -> None:
    await grant_org_role(db, OTHER.id, "admin")
    await set_member_limit(db, TEST_USER_ID, "10")
    await set_agent_limit(db, agent, "20")
    await spend(db, "16", agent_id=agent)  # user 160% (straight to 100), agent 80%
    await _admit(db, agent)
    found = {
        (payload["scope"], payload["threshold"], user, org)
        for user, org, payload in await _warnings()
    }
    assert found == {
        ("user", 100, TEST_USER_ID, TEST_ORG_ID),
        ("user", 100, OTHER.id, TEST_ORG_ID),
        ("agent", 80, OTHER.id, TEST_ORG_ID),
    }


async def test_warning_is_listed_with_its_target_in_the_bell(
    client: AsyncClient, test_app: FastAPI, db: AsyncSession, agent: UUID
) -> None:
    await set_member_limit(db, OTHER.id, "1")
    await spend(db, "1", user_id=OTHER.id)
    try:
        await admit_run(db, attribution(agent, user_id=OTHER.id), enforce=True)
    except AppError:
        await db.rollback()
    with acting_as(test_app, OTHER, TEST_ORG_ID):
        page = (await client.get("/api/notifications")).json()
    item = page["items"][0]
    assert item["kind"] == "usage.limit_warning" and item["link"] == "/settings/usage"
    assert item["target"]["type"] == "usage_limit" and item["target"]["accessible"] is True
    assert item["payload"]["scope"] == "user" and item["payload"]["threshold"] == 100
