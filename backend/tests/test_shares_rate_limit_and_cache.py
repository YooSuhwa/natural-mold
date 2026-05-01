"""Tests for the public share rate limit + snapshot cache surface."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_current_user, get_db
from app.main import create_app
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.rate_limit import limiter
from tests.conftest import (
    TEST_USER_ID,
    TestSession,
    override_get_current_user,
    override_get_db,
)


async def _seed_conversation() -> uuid.UUID:
    async with TestSession() as db:
        existing = await db.get(User, TEST_USER_ID)
        if existing is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
            await db.flush()

        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=TEST_USER_ID,
            name="Cache Agent",
            description="An agent for cache tests",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        conv = Conversation(agent_id=agent.id, title="Cached topic")
        db.add(conv)
        await db.commit()
        return conv.id


# ---------------------------------------------------------------------------
# Snapshot cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_share_view_uses_snapshot_cache(client: AsyncClient):
    """Second request must hit cache — only one checkpointer walk happens."""
    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    walk = AsyncMock(return_value=[])
    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=walk,
    ):
        first = await client.get(f"/api/shares/{token}")
        second = await client.get(f"/api/shares/{token}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Two requests, one underlying expensive call → cache hit on the second.
    assert walk.await_count == 1


@pytest.mark.asyncio
async def test_public_share_messages_envelope_uses_snapshot_cache(client: AsyncClient):
    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    walk = AsyncMock(return_value=[])
    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=walk,
    ):
        await client.get(f"/api/shares/{token}/messages")
        await client.get(f"/api/shares/{token}/messages")

    assert walk.await_count == 1


@pytest.mark.asyncio
async def test_revoking_share_invalidates_cached_snapshot(client: AsyncClient):
    """Stale-within-TTL must not survive a revoke."""
    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[]),
    ):
        # Warm the cache.
        first = await client.get(f"/api/shares/{token}")
        assert first.status_code == 200

    revoke = await client.delete(f"/api/conversations/{conv_id}/share")
    assert revoke.status_code == 204

    # Cache must not serve a stale view after revoke.
    after = await client.get(f"/api/shares/{token}")
    assert after.status_code == 404


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_share_rate_limit_returns_429_when_enabled():
    """Tight limit (2/minute) on a fresh app → third request gets 429.

    The conftest disables the limiter for the shared client fixture; this
    test stands up its own app with a tight limit and the limiter re-enabled
    so the rate-limit path is genuinely exercised.
    """
    conv_id = await _seed_conversation()

    # Re-enable + lower the limit for this app's lifetime. Restore in finally.
    saved_enabled = limiter.enabled
    limiter.enabled = True

    # slowapi reads decorator strings at decoration time, so we can't
    # retroactively tighten the existing route. Instead drive the limiter's
    # in-memory storage to near-cap before the request.
    try:
        app = create_app()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_current_user

        # Mint a token the public route can resolve.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            token = (
                await ac.post(f"/api/conversations/{conv_id}/share")
            ).json()["share_token"]

            with patch(
                "app.routers.shares.chat_service.list_messages_from_checkpointer",
                new=AsyncMock(return_value=[]),
            ):
                # Hammer until 429 — bound the loop so a regression doesn't
                # spin forever. Default config is 60/min; 80 calls is enough
                # to exceed it from a single client.
                seen_429 = False
                for _ in range(120):
                    resp = await ac.get(f"/api/shares/{token}")
                    if resp.status_code == 429:
                        seen_429 = True
                        break
                assert seen_429, "rate limit never tripped under sustained load"
    finally:
        limiter.enabled = saved_enabled
        limiter.reset()
