"""H-06 run-start gate: four spend tiers, company runs and rate (plan 8.7, 8.4 quota row)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.daily_spend_agent import DailySpendAgent
from app.tenancy import limits
from app.tenancy.limits import admit_run
from tests.authz.e01_support import add_agent
from tests.authz.e08_support import add_conversation
from tests.authz.h03_support import (
    OTHER,
    TEST_ORG_ID,
    TEST_TENANT_ID,
    TEST_USER_ID,
    grant_company_role,
    seed_world,
)
from tests.authz.h06_support import (
    attribution,
    fresh_limits,
    set_agent_limit,
    set_member_limit,
    set_org_limit,
    set_tenant_limits,
    spend,
)

__all__ = ["fresh_limits"]
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_limits")]


@pytest.fixture
async def agent(db: AsyncSession) -> Agent:
    await seed_world(db)
    return await add_agent(db)


async def _refused(db: AsyncSession, agent_id: UUID, user_id: UUID = TEST_USER_ID) -> AppError:
    with pytest.raises(AppError) as caught:
        await admit_run(db, attribution(agent_id, user_id=user_id), enforce=True)
    error = caught.value
    assert (error.status, error.code) == (429, "quota_exceeded")
    return error


async def test_no_limits_admits(db: AsyncSession, agent: Agent) -> None:
    await spend(db, "1000", agent_id=agent.id)
    await admit_run(db, attribution(agent.id), enforce=True)


@pytest.mark.parametrize("scope", ["tenant", "org", "user", "agent"])
async def test_each_tier_blocks_at_100_percent_with_its_scope(
    db: AsyncSession, agent: Agent, scope: str
) -> None:
    await spend(db, "10", agent_id=agent.id)
    if scope == "tenant":
        await set_tenant_limits(db, monthly_spend_limit="10")
    elif scope == "org":
        await set_org_limit(db, "10")
    elif scope == "user":
        await set_member_limit(db, TEST_USER_ID, "10")
    else:
        await set_agent_limit(db, agent.id, "10")
    details = (await _refused(db, agent.id)).details or {}
    assert (details["kind"], details["scope"]) == ("monthly_spend", scope)
    resets_at = details["resets_at"]
    assert isinstance(resets_at, datetime) and resets_at.tzinfo is not None
    # A plain member sees amounts only for their own limit.
    assert ("limit" in details, "used" in details) == ((scope == "user"),) * 2
    await grant_company_role(db, TEST_USER_ID, "auditor")
    details = (await _refused(db, agent.id)).details or {}
    assert (details["limit"], details["used"]) == (10.0, 10.0)


async def test_company_is_reported_first(db: AsyncSession, agent: Agent) -> None:
    await spend(db, "5", agent_id=agent.id)
    await set_tenant_limits(db, monthly_spend_limit="5")
    await set_agent_limit(db, agent.id, "1")
    assert ((await _refused(db, agent.id)).details or {})["scope"] == "tenant"
    await set_tenant_limits(db, monthly_spend_limit="100")
    await set_agent_limit(db, agent.id, "100")
    await admit_run(db, attribution(agent.id), enforce=True)


async def test_user_limit_counts_only_that_organization_and_falls_back_to_default(
    db: AsyncSession, agent: Agent
) -> None:
    from tests.authz.d04_support import OTHER_ORG_ID

    await spend(db, "50", org_id=OTHER_ORG_ID)  # same user, another organization
    await set_member_limit(db, TEST_USER_ID, "10")
    await admit_run(db, attribution(agent.id), enforce=True)
    await set_member_limit(db, TEST_USER_ID, None)
    from app.models.organization import Organization

    org = await db.get(Organization, TEST_ORG_ID)
    assert org is not None
    org.settings = {**org.settings, "default_member_limit": "2"}
    await db.commit()
    await spend(db, "2", user_id=OTHER.id)
    limits.reset_cache()
    assert ((await _refused(db, agent.id, OTHER.id)).details or {})["scope"] == "user"
    await admit_run(db, attribution(agent.id), enforce=True)  # me: 0 of 2


async def test_resume_is_not_refused_at_start(db: AsyncSession, agent: Agent) -> None:
    await spend(db, "10", agent_id=agent.id)
    await set_org_limit(db, "10")
    await admit_run(db, attribution(agent.id), enforce=False)


async def test_cache_is_skipped_at_80_percent(db: AsyncSession, agent: Agent) -> None:
    await set_agent_limit(db, agent.id, "10")
    await spend(db, "1", agent_id=agent.id)
    subject = limits.subject_of(attribution(agent.id))
    await limits.UsageLimits.states(db, subject)
    await db.execute(
        update(DailySpendAgent).values(total_cost_usd=Decimal("8"))
    )  # booked without clearing the cache
    await db.commit()
    (cached,) = await limits.UsageLimits.states(db, subject)
    assert cached.used == 1  # below 80%: the 10 s cache answers
    limits.reset_cache()
    assert (await limits.UsageLimits.states(db, subject))[0].used == 8
    await db.execute(update(DailySpendAgent).values(total_cost_usd=Decimal("10")))
    await db.commit()
    assert ((await _refused(db, agent.id)).details or {})["scope"] == "agent"  # ≥ 80%: fresh


async def test_concurrent_runs_and_request_rate(
    db: AsyncSession, agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await add_conversation(
        db, agent, user=TEST_USER_ID, last_activity=datetime.now(UTC)
    )
    db.add(
        ConversationRun(
            conversation_id=conversation_id,
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            source="chat",
            status="running",
            is_active=True,
        )
    )
    await db.commit()
    await set_tenant_limits(db, concurrent_run_limit=1)
    details = (await _refused(db, agent.id)).details or {}
    assert (details["kind"], details["scope"], details["used"]) == ("concurrent_runs", "tenant", 1)

    await set_tenant_limits(db, concurrent_run_limit=None, request_rate_limit=2)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(3):  # rate limiting off: never counted
        await admit_run(db, attribution(agent.id), enforce=True)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    for _ in range(2):
        await admit_run(db, attribution(agent.id), enforce=True)
    assert ((await _refused(db, agent.id)).details or {})["kind"] == "rate"


async def test_chat_start_answers_429_with_the_scope(
    client: AsyncClient, db: AsyncSession, agent: Agent
) -> None:
    await spend(db, "3", agent_id=agent.id)
    await set_agent_limit(db, agent.id, "3")
    response = await client.post(
        f"/api/agents/{agent.id}/conversations/start", json={"content": "hi"}
    )
    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["code"] == "quota_exceeded"
    assert (error["details"]["kind"], error["details"]["scope"]) == ("monthly_spend", "agent")
    assert set(error["details"]) == {"kind", "scope", "resets_at"}  # owner, no oversight
