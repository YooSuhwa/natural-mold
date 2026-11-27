"""Every search provider attempt checks current authority and the organization budget."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrganizationMember
from app.models.research_search_usage import ResearchSearchUsage
from app.services import research_search_policy as search
from tests.sqlite_test_engine import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]

ENDPOINT = "https://search.example.com/team/search"


@pytest.fixture
def actor(monkeypatch: pytest.MonkeyPatch, org_fixture: OrgFixture) -> search.ResearchSearchActor:
    monkeypatch.setattr(search, "tenant_session", lambda _: TestSession())
    return search.ResearchSearchActor(org_fixture.tenant, org_fixture.first, org_fixture.user.id)


async def set_policy(
    db: AsyncSession, actor: search.ResearchSearchActor, **changes: JsonValue
) -> None:
    org = await db.get(Organization, actor.org_id)
    assert org is not None
    policy: dict[str, JsonValue] = {
        "providers": ["tavily", "searxng"],
        "searxng_endpoint": ENDPOINT,
        "monthly_attempt_limit": 2,
        **changes,
    }
    org.settings = {"research_search": policy}
    await db.commit()


async def attempt_count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.sum(ResearchSearchUsage.attempts))) or 0)


async def test_unconfigured_org_keeps_only_existing_tavily_contract(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    assert await search.reserve_attempt(actor, "tavily", legacy_tavily=True) is None
    with pytest.raises(search.SearchPolicyError, match="not enabled"):
        await search.load_plan(actor)
    with pytest.raises(search.SearchPolicyError, match="not enabled"):
        await search.reserve_attempt(actor, "tavily")
    with pytest.raises(search.SearchPolicyError, match="not enabled"):
        await search.reserve_attempt(actor, "searxng", legacy_tavily=True)
    with pytest.raises(search.SearchPolicyError, match="authorized organization"):
        await search.reserve_attempt(None, "tavily", legacy_tavily=True)
    assert await attempt_count(db) == 0


async def test_primary_and_fallback_share_one_monthly_budget(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    await set_policy(db, actor)
    assert await search.load_plan(actor) == ("tavily", "searxng")
    assert await search.reserve_attempt(actor, "tavily") is None
    assert await search.reserve_attempt(actor, "searxng") == ENDPOINT
    with pytest.raises(search.SearchPolicyError, match="budget is exhausted"):
        await search.reserve_attempt(actor, "tavily", legacy_tavily=True)
    assert await attempt_count(db) == 2


async def test_explicit_policy_also_controls_legacy_tavily_and_membership_is_live(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    await set_policy(db, actor, providers=["searxng"])
    with pytest.raises(search.SearchPolicyError, match="not permitted"):
        await search.reserve_attempt(actor, "tavily", legacy_tavily=True)
    assert await search.load_plan(actor) == ("searxng",)
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == actor.org_id, OrganizationMember.user_id == actor.user_id
        )
        .values(status="suspended")
    )
    await db.commit()
    with pytest.raises(search.SearchPolicyError, match="not authorized"):
        await search.reserve_attempt(actor, "searxng")
    assert await attempt_count(db) == 0


async def test_wrong_tenant_or_organization_cannot_read_plan_or_spend(
    db: AsyncSession, actor: search.ResearchSearchActor, org_fixture: OrgFixture
) -> None:
    await set_policy(db, actor)
    other = await db.get(Organization, org_fixture.second)
    assert other is not None
    for forged in (
        replace(actor, tenant_id=other.tenant_id),
        replace(actor, org_id=org_fixture.foreign),
    ):
        with pytest.raises(search.SearchPolicyError, match="not authorized"):
            await search.load_plan(forged)
        with pytest.raises(search.SearchPolicyError, match="not authorized"):
            await search.reserve_attempt(forged, "searxng")
    assert await attempt_count(db) == 0


async def test_zero_search_or_exhausted_spend_limit_blocks_authorized_provider(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    await set_policy(db, actor, monthly_attempt_limit=0)
    with pytest.raises(search.SearchPolicyError, match="budget is exhausted"):
        await search.reserve_attempt(actor, "searxng")
    await set_policy(db, actor)
    await db.execute(
        update(Organization).where(Organization.id == actor.org_id).values(monthly_spend_limit=0)
    )
    await db.commit()
    with pytest.raises(search.SearchPolicyError, match="spend limit is exhausted"):
        await search.reserve_attempt(actor, "tavily")
    assert await attempt_count(db) == 0


async def test_malformed_stored_policy_rejects_default_without_exposing_configuration(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    await set_policy(db, actor, providers=["untrusted-provider"])
    with pytest.raises(
        search.SearchPolicyError, match="^Organization research search policy is invalid$"
    ):
        await search.reserve_attempt(actor, "tavily", legacy_tavily=True)
    assert await attempt_count(db) == 0


async def test_quota_reset_is_independent_of_previous_billing_month(
    db: AsyncSession, actor: search.ResearchSearchActor
) -> None:
    await set_policy(db, actor, monthly_attempt_limit=1)
    db.add(
        ResearchSearchUsage(
            tenant_id=actor.tenant_id,
            org_id=actor.org_id,
            month_start=date(2000, 1, 1),
            attempts=40,
        )
    )
    await db.commit()
    assert await search.reserve_attempt(actor, "tavily") is None
    with pytest.raises(search.SearchPolicyError, match="budget is exhausted"):
        await search.reserve_attempt(actor, "searxng")
    assert await attempt_count(db) == 41
