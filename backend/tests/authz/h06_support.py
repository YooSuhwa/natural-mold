"""Seeds for the H-06 usage limit tests (plan 8.7)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_user import DailySpendUser
from app.models.organization import Organization, OrganizationMember, Tenant
from app.tenancy import limit_alerts, limit_capacity, limits
from app.tenancy.attribution import RunAttribution
from tests.authz.h03_support import TEST_ORG_ID, TEST_TENANT_ID, TEST_USER_ID
from tests.conftest import TestSession


@pytest.fixture
def fresh_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Empty limit caches and route the alert writer to the test database."""
    monkeypatch.setattr(limit_alerts, "async_session", TestSession)
    limits.reset_cache()
    limit_alerts.reset_sent()
    limit_capacity.reset_rate_windows()
    yield
    limits.reset_cache()
    limit_alerts.reset_sent()
    limit_capacity.reset_rate_windows()


async def set_tenant_limits(db: AsyncSession, **values: JsonValue) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.limits = {**tenant.limits, **values}
    await db.commit()
    limits.reset_cache()


async def set_org_limit(db: AsyncSession, value: str | None, org_id: UUID = TEST_ORG_ID) -> None:
    org = await db.get(Organization, org_id)
    assert org is not None
    org.monthly_spend_limit = None if value is None else Decimal(value)
    await db.commit()
    limits.reset_cache()


async def set_member_limit(db: AsyncSession, user_id: UUID, value: str | None) -> None:
    member = await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.org_id == TEST_ORG_ID, OrganizationMember.user_id == user_id
        )
    )
    assert member is not None
    member.monthly_spend_limit = None if value is None else Decimal(value)
    await db.commit()
    limits.reset_cache()


async def set_agent_limit(db: AsyncSession, agent_id: UUID, value: str | None) -> None:
    agent = await db.get(Agent, agent_id)
    assert agent is not None
    agent.monthly_spend_limit = None if value is None else Decimal(value)
    await db.commit()
    limits.reset_cache()


async def spend(
    db: AsyncSession,
    cost: str,
    *,
    user_id: UUID = TEST_USER_ID,
    agent_id: UUID | None = None,
    org_id: UUID = TEST_ORG_ID,
) -> None:
    """Add ``cost`` to today's rows of the user (and the agent) in ``org_id``."""
    today = datetime.now(UTC).date()  # always inside the current counted window
    common = {"tenant_id": TEST_TENANT_ID, "org_id": org_id, "date": today}
    targets: list[tuple[type[DailySpendUser] | type[DailySpendAgent], dict[str, UUID]]] = [
        (DailySpendUser, {"user_id": user_id})
    ]
    if agent_id is not None:
        targets.append((DailySpendAgent, {"agent_id": agent_id}))
    for table, key in targets:
        row = await db.scalar(
            select(table).filter_by(org_id=org_id, date=today, **key)  # type: ignore[arg-type]
        )
        if row is None:
            db.add(table(**common, **key, total_cost_usd=Decimal(cost), request_count=1))
        else:
            row.total_cost_usd += Decimal(cost)
    await db.commit()
    limits.reset_cache()


def attribution(agent_id: UUID, *, user_id: UUID = TEST_USER_ID) -> RunAttribution:
    return RunAttribution(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        run_source="CHAT",
        invoker_type="user",
        caller_user_id=user_id,
        actor_agent_id=agent_id,
        resource_owner_user_id=TEST_USER_ID,
        on_behalf_of_user_id=user_id,
        credential_subject_user_id=user_id,
    )
