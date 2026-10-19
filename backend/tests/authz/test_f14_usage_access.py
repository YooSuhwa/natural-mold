"""F-14 / D-07 deviation closed (9.6 "내 사용분은 누구나"): runners see their own usage.

``GET /api/agents/{id}/usage``: ``can_view_config`` (and the legacy owner) see the whole
agent; a runner with only ``can_run`` sees the rows they were billed for.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.models.token_usage import TokenUsage
from app.services import usage_aggregate, usage_service
from tests.authz.agent_route_fixture import ME, OTHER_USER_ID, authz_override, enforce, seed_agent
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio
BEARER = {"Authorization": f"Bearer {create_access_token(TEST_USER_ID)}"}


async def _usage(agent_id: str) -> None:
    async with TestSession() as db:
        for caller, tokens in ((TEST_USER_ID, 3), (OTHER_USER_ID, 40), (None, 500)):
            db.add(
                TokenUsage(
                    conversation_id=uuid.uuid4(),
                    agent_id=uuid.UUID(agent_id),
                    caller_user_id=caller,
                    model_name="m",
                    prompt_tokens=tokens,
                    completion_tokens=0,
                    total_tokens=tokens,
                    estimated_cost=Decimal("0.01"),
                )
            )
        await db.commit()


async def test_runner_sees_only_their_own_usage(client: AsyncClient, test_app: FastAPI) -> None:
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    await _usage(agent_id)
    agent = uuid.UUID(agent_id)
    url = f"/api/agents/{agent_id}/usage"
    with authz_override(test_app, enforce((ME, "can_run", agent))):
        runner = (await client.get(url, headers=BEARER)).json()
    with authz_override(test_app, enforce((ME, "can_run", agent), (ME, "can_view_config", agent))):
        viewer = (await client.get(url, headers=BEARER)).json()
    assert (runner["total_tokens"], runner["estimated_cost_usd"]) == (3, 0.01)
    assert viewer["total_tokens"] == 543


async def test_legacy_owner_still_sees_the_whole_agent(client: AsyncClient) -> None:
    agent_id = await seed_agent(owner=TEST_USER_ID)
    await _usage(agent_id)
    response = await client.get(f"/api/agents/{agent_id}/usage", headers=BEARER)
    assert response.status_code == 200 and response.json()["total_tokens"] == 543


async def test_summary_breakdown_is_the_users_own_usage(db: AsyncSession) -> None:
    """Owners don't see a runner's cost in their ``by_agent``; runners see their share."""
    owned = await seed_agent(owner=TEST_USER_ID)
    shared = await seed_agent(owner=OTHER_USER_ID)
    await _usage(owned)  # me 3, other caller 40, unattributed 500
    await _usage(shared)
    mine = await usage_service.get_usage_summary(db, TEST_USER_ID)
    theirs = await usage_service.get_usage_summary(db, OTHER_USER_ID)
    assert {row["agent_id"]: row["total_tokens"] for row in mine["by_agent"]} == {
        owned: 3,
        shared: 3,
    }
    assert {row["agent_id"]: row["total_tokens"] for row in theirs["by_agent"]} == {
        owned: 40,
        shared: 40,
    }
    daily = await usage_aggregate.get_daily_spend(
        db, user_id=TEST_USER_ID, target_kind="agent", group_by="target"
    )
    assert sorted(row["total_tokens_in"] for row in daily) == [3, 3]
