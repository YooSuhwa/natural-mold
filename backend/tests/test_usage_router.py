"""Tests for app.routers.usage — agent usage and summary endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.token_usage import TokenUsage
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_agent_with_usage() -> uuid.UUID:
    """Create a full chain: User -> Model -> Agent -> Conv -> Msg -> TokenUsage."""
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=user.id,
            name="Usage Agent",
            system_prompt="Hi",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        conv = Conversation(agent_id=agent.id, title="Conv")
        db.add(conv)
        await db.flush()

        usage = TokenUsage(
            conversation_id=conv.id,
            agent_id=agent.id,
            model_name="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated_cost=Decimal("0.005"),
        )
        db.add(usage)

        # Mirror to daily_spend_* — get_usage_summary now reads from there.
        from datetime import date as date_type

        from app.models.daily_spend_agent import DailySpendAgent
        from app.models.daily_spend_user import DailySpendUser

        today = date_type.today()
        db.add(
            DailySpendAgent(
                date=today,
                agent_id=agent.id,
                total_tokens_in=100,
                total_tokens_out=50,
                total_cost_usd=Decimal("0.005"),
                request_count=1,
            )
        )
        db.add(
            DailySpendUser(
                date=today,
                user_id=user.id,
                total_tokens_in=100,
                total_tokens_out=50,
                total_cost_usd=Decimal("0.005"),
                request_count=1,
            )
        )
        await db.commit()
        return agent.id


async def _seed_agent_no_usage() -> uuid.UUID:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=user.id,
            name="Empty Agent",
            system_prompt="Hi",
            model_id=model.id,
        )
        db.add(agent)
        await db.commit()
        return agent.id


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_usage(client: AsyncClient):
    agent_id = await _seed_agent_with_usage()

    resp = await client.get(f"/api/agents/{agent_id}/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == str(agent_id)
    assert data["total_tokens"] == 150
    assert data["period"] == "all"


@pytest.mark.asyncio
async def test_get_agent_usage_with_period(client: AsyncClient):
    agent_id = await _seed_agent_with_usage()

    resp = await client.get(f"/api/agents/{agent_id}/usage?period=week")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "week"


# ---------------------------------------------------------------------------
# GET /api/usage/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_usage_summary(client: AsyncClient):
    await _seed_agent_with_usage()

    resp = await client.get("/api/usage/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tokens"] == 150
    assert data["period"] == "all"
    assert len(data["by_agent"]) == 1


@pytest.mark.asyncio
async def test_get_usage_summary_with_period(client: AsyncClient):
    await _seed_agent_with_usage()

    resp = await client.get("/api/usage/summary?period=month")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "month"
