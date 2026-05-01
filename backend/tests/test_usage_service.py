"""Tests for app.services.usage_service — token usage aggregation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.usage_service import get_agent_usage, get_usage_summary
from tests.conftest import TEST_USER_ID


async def _seed_user_and_model(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a User and Model, return (user_id, model_id)."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    db.add(model)
    await db.flush()
    return user.id, model.id


async def _seed_agent(
    db: AsyncSession, user_id: uuid.UUID, model_id: uuid.UUID, name: str = "Agent"
) -> uuid.UUID:
    agent = Agent(
        user_id=user_id,
        name=name,
        system_prompt="You are helpful.",
        model_id=model_id,
    )
    db.add(agent)
    await db.flush()
    return agent.id


async def _seed_usage(
    db: AsyncSession,
    agent_id: uuid.UUID,
    prompt: int,
    completion: int,
    cost: float,
) -> None:
    """Create a Conversation + TokenUsage record + matching DailySpend rows.

    The summary now reads from ``daily_spend_*`` (m21 spend writer) so test
    fixtures must seed both the legacy raw table and the aggregate tables to
    keep both `get_agent_usage` and `get_usage_summary` happy.
    """
    from datetime import date as date_type

    from app.models.agent import Agent as AgentModel
    from app.models.daily_spend_agent import DailySpendAgent
    from app.models.daily_spend_user import DailySpendUser

    conv = Conversation(agent_id=agent_id, title="c")
    db.add(conv)
    await db.flush()

    usage = TokenUsage(
        conversation_id=conv.id,
        agent_id=agent_id,
        model_name="gpt-4o",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        estimated_cost=Decimal(str(cost)),
    )
    db.add(usage)

    today = date_type.today()
    agent = (
        await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    ).scalar_one()
    user_id = agent.user_id

    # Upsert per (date, agent) and (date, user) so callers can seed multiple
    # rows for the same fixture without tripping the unique constraint.
    existing_agent = (
        await db.execute(
            select(DailySpendAgent).where(
                DailySpendAgent.date == today,
                DailySpendAgent.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if existing_agent:
        existing_agent.total_tokens_in += prompt
        existing_agent.total_tokens_out += completion
        existing_agent.total_cost_usd += Decimal(str(cost))
        existing_agent.request_count += 1
    else:
        db.add(
            DailySpendAgent(
                date=today,
                agent_id=agent_id,
                total_tokens_in=prompt,
                total_tokens_out=completion,
                total_cost_usd=Decimal(str(cost)),
                request_count=1,
            )
        )

    existing_user = (
        await db.execute(
            select(DailySpendUser).where(
                DailySpendUser.date == today,
                DailySpendUser.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing_user:
        existing_user.total_tokens_in += prompt
        existing_user.total_tokens_out += completion
        existing_user.total_cost_usd += Decimal(str(cost))
        existing_user.request_count += 1
    else:
        db.add(
            DailySpendUser(
                date=today,
                user_id=user_id,
                total_tokens_in=prompt,
                total_tokens_out=completion,
                total_cost_usd=Decimal(str(cost)),
                request_count=1,
            )
        )
    await db.flush()


# ---------------------------------------------------------------------------
# get_agent_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_usage_with_data(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    agent_id = await _seed_agent(db, user_id, model_id)
    await _seed_usage(db, agent_id, prompt=100, completion=50, cost=0.005)
    await _seed_usage(db, agent_id, prompt=200, completion=100, cost=0.01)
    await db.commit()

    result = await get_agent_usage(db, agent_id)

    assert result["agent_id"] == str(agent_id)
    assert result["prompt_tokens"] == 300
    assert result["completion_tokens"] == 150
    assert result["total_tokens"] == 450
    assert result["estimated_cost_usd"] == pytest.approx(0.015)
    assert result["period"] == "all"


@pytest.mark.asyncio
async def test_get_agent_usage_no_records(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    agent_id = await _seed_agent(db, user_id, model_id)
    await db.commit()

    result = await get_agent_usage(db, agent_id)

    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["estimated_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_get_agent_usage_with_period(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    agent_id = await _seed_agent(db, user_id, model_id)
    await db.commit()

    result = await get_agent_usage(db, agent_id, period="7d")

    assert result["period"] == "7d"


# ---------------------------------------------------------------------------
# get_usage_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_usage_summary_multiple_agents(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    agent1_id = await _seed_agent(db, user_id, model_id, name="Agent A")
    agent2_id = await _seed_agent(db, user_id, model_id, name="Agent B")
    await _seed_usage(db, agent1_id, prompt=100, completion=50, cost=0.005)
    await _seed_usage(db, agent2_id, prompt=200, completion=100, cost=0.01)
    await db.commit()

    result = await get_usage_summary(db, user_id)

    assert result["prompt_tokens"] == 300
    assert result["completion_tokens"] == 150
    assert result["total_tokens"] == 450
    assert result["estimated_cost_usd"] == pytest.approx(0.015)
    assert len(result["by_agent"]) == 2
    agent_names = {a["agent_name"] for a in result["by_agent"]}
    assert agent_names == {"Agent A", "Agent B"}


@pytest.mark.asyncio
async def test_get_usage_summary_empty(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    await db.commit()

    result = await get_usage_summary(db, user_id)

    assert result["total_tokens"] == 0
    assert result["estimated_cost_usd"] == 0.0
    assert result["by_agent"] == []


@pytest.mark.asyncio
async def test_get_usage_summary_with_period(db: AsyncSession):
    user_id, model_id = await _seed_user_and_model(db)
    await db.commit()

    result = await get_usage_summary(db, user_id, period="30d")

    assert result["period"] == "30d"
