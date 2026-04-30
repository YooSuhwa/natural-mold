"""Tests for ``GET /api/usage/daily`` + ``usage_aggregate.get_daily_spend``.

We seed the daily aggregate tables directly (no need to run the queue) and
verify that the router projects the rows correctly across the three axes
plus the ``group_by`` modes.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_basic() -> dict[str, uuid.UUID]:
    """Common fixture: 1 user, 2 models, 2 agents, 3 days of spend."""

    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model_a = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        model_b = Model(
            provider="anthropic", model_name="claude-sonnet", display_name="Claude Sonnet"
        )
        db.add_all([model_a, model_b])
        await db.flush()

        agent_a = Agent(
            user_id=user.id, name="Agent A", system_prompt="hi", model_id=model_a.id
        )
        agent_b = Agent(
            user_id=user.id, name="Agent B", system_prompt="hi", model_id=model_b.id
        )
        db.add_all([agent_a, agent_b])
        await db.flush()

        today = date_type.today()
        yesterday = today - timedelta(days=1)
        two_days = today - timedelta(days=2)

        # Per-user: one row per day.
        for d, ti, to_, cost in [
            (two_days, 100, 50, "0.0010"),
            (yesterday, 200, 100, "0.0020"),
            (today, 400, 200, "0.0040"),
        ]:
            db.add(
                DailySpendUser(
                    date=d,
                    user_id=user.id,
                    total_tokens_in=ti,
                    total_tokens_out=to_,
                    total_cost_usd=Decimal(cost),
                    request_count=2,
                )
            )

        # Per-agent: split between agent_a / agent_b on today.
        db.add(
            DailySpendAgent(
                date=today,
                agent_id=agent_a.id,
                total_tokens_in=300,
                total_tokens_out=150,
                total_cost_usd=Decimal("0.0030"),
                request_count=1,
            )
        )
        db.add(
            DailySpendAgent(
                date=today,
                agent_id=agent_b.id,
                total_tokens_in=100,
                total_tokens_out=50,
                total_cost_usd=Decimal("0.0010"),
                request_count=1,
            )
        )

        # Per-model: matching rows on today.
        db.add(
            DailySpendModel(
                date=today,
                model_id=model_a.id,
                total_tokens_in=300,
                total_tokens_out=150,
                total_cost_usd=Decimal("0.0030"),
                request_count=1,
            )
        )
        db.add(
            DailySpendModel(
                date=today,
                model_id=model_b.id,
                total_tokens_in=100,
                total_tokens_out=50,
                total_cost_usd=Decimal("0.0010"),
                request_count=1,
            )
        )

        await db.commit()
        return {
            "user_id": user.id,
            "agent_a": agent_a.id,
            "agent_b": agent_b.id,
            "model_a": model_a.id,
            "model_b": model_b.id,
        }


@pytest.mark.asyncio
async def test_user_axis_returns_three_days(client: AsyncClient) -> None:
    await _seed_basic()
    resp = await client.get("/api/usage/daily?target_kind=user")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 3
    # Sorted oldest → newest
    assert data[0]["date"] < data[-1]["date"]
    assert data[-1]["total_tokens_in"] == 400
    assert data[-1]["target_id"] is None


@pytest.mark.asyncio
async def test_agent_axis_group_by_target_resolves_labels(client: AsyncClient) -> None:
    seeds = await _seed_basic()
    resp = await client.get("/api/usage/daily?target_kind=agent&group_by=target")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Two agent rows on today.
    today = date_type.today().isoformat()
    today_rows = [r for r in data if r["date"] == today]
    assert len(today_rows) == 2
    labels = {row["target_label"] for row in today_rows}
    assert "Agent A" in labels and "Agent B" in labels
    expected_ids = {str(seeds["agent_a"]), str(seeds["agent_b"])}
    assert all(row["target_id"] in expected_ids for row in today_rows)


@pytest.mark.asyncio
async def test_model_axis_scoped_through_agent(client: AsyncClient) -> None:
    """The model axis joins through Agent so cross-tenant rows don't leak."""

    await _seed_basic()
    resp = await client.get("/api/usage/daily?target_kind=model&group_by=target")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    today = date_type.today().isoformat()
    today_rows = [r for r in data if r["date"] == today]
    # Both models should appear with their display names.
    labels = {row["target_label"] for row in today_rows}
    assert "GPT-4o" in labels and "Claude Sonnet" in labels


@pytest.mark.asyncio
async def test_window_filters_apply(client: AsyncClient) -> None:
    await _seed_basic()
    today = date_type.today()
    yesterday = today - timedelta(days=1)
    resp = await client.get(
        f"/api/usage/daily?target_kind=user&from={yesterday.isoformat()}"
        f"&to={today.isoformat()}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2  # excluded the 2-days-ago row


@pytest.mark.asyncio
async def test_target_id_filter(client: AsyncClient) -> None:
    seeds = await _seed_basic()
    resp = await client.get(
        f"/api/usage/daily?target_kind=agent&target_id={seeds['agent_a']}"
    )
    assert resp.status_code == 200
    data = resp.json()
    today = date_type.today().isoformat()
    rows = [r for r in data if r["date"] == today]
    assert len(rows) == 1
    assert rows[0]["total_tokens_in"] == 300


@pytest.mark.asyncio
async def test_user_isolation(client: AsyncClient) -> None:
    """Daily rows for a different user must not leak into the response."""

    seeds = await _seed_basic()
    other_user = uuid.uuid4()
    async with TestSession() as db:
        db.add(User(id=other_user, email="other@example.com", name="Other"))
        db.add(
            DailySpendUser(
                date=date_type.today(),
                user_id=other_user,
                total_tokens_in=99999,
                total_tokens_out=99999,
                total_cost_usd=Decimal("9.99"),
                request_count=1,
            )
        )
        await db.commit()

    resp = await client.get("/api/usage/daily?target_kind=user")
    assert resp.status_code == 200
    data = resp.json()
    # Only the test user's three rows; the foreign 99999 row is hidden.
    assert all(row["total_tokens_in"] != 99999 for row in data)
    # Use seeds variable so flake8 stays happy.
    assert seeds["user_id"] == TEST_USER_ID
