"""Tests for app.routers.assistant — POST /api/agents/{id}/assistant/message."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_agent() -> uuid.UUID:
    """Create User + Model + Agent. Return agent_id."""
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=user.id,
            name="Assistant Agent",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.commit()
        return agent.id


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/assistant/message — success (SSE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_message(client: AsyncClient):
    """POST /api/agents/{id}/assistant/message → SSE streaming response."""
    agent_id = await _seed_agent()

    async def mock_stream(*args, **kwargs):
        yield 'event: message_start\ndata: {"id": "m1", "role": "assistant"}\n\n'
        yield 'event: content_delta\ndata: {"delta": "Hello"}\n\n'
        yield 'event: message_end\ndata: {"content": "Hello", "usage": {}}\n\n'

    with patch(
        "app.services.assistant_service.stream_assistant_message",
        side_effect=mock_stream,
    ):
        resp = await client.post(
            f"/api/agents/{agent_id}/assistant/message",
            json={"content": "시스템 프롬프트 수정해줘"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "message_start" in body
    assert "content_delta" in body
    assert "message_end" in body


# ---------------------------------------------------------------------------
# POST — agent not found → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_message_agent_not_found(client: AsyncClient):
    """Non-existent agent returns 404."""
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(
        f"/api/agents/{fake_id}/assistant/message",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST — with session_id → thread_id uses session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_message_with_session_id(client: AsyncClient):
    """When session_id is provided, thread_id includes it."""
    agent_id = await _seed_agent()

    captured_kwargs: dict = {}

    async def mock_stream(**kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    with patch(
        "app.services.assistant_service.stream_assistant_message",
        side_effect=mock_stream,
    ):
        resp = await client.post(
            f"/api/agents/{agent_id}/assistant/message",
            json={"content": "test", "session_id": "my-session-abc"},
        )

    assert resp.status_code == 200
    assert captured_kwargs["thread_id"] == f"assistant_{agent_id}_my-session-abc"


# ---------------------------------------------------------------------------
# POST — without session_id → thread_id fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_message_without_session_id(client: AsyncClient):
    """When session_id is absent, thread_id falls back to agent_id only."""
    agent_id = await _seed_agent()

    captured_kwargs: dict = {}

    async def mock_stream(**kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    with patch(
        "app.services.assistant_service.stream_assistant_message",
        side_effect=mock_stream,
    ):
        resp = await client.post(
            f"/api/agents/{agent_id}/assistant/message",
            json={"content": "test"},
        )

    assert resp.status_code == 200
    assert captured_kwargs["thread_id"] == f"assistant_{agent_id}"
