"""Tests for app.routers.feedback — message feedback toggle/upsert."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation() -> uuid.UUID:
    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="t@t.com", name="t"))
        model = Model(provider="openai", model_name="x", display_name="X")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID, name="A", system_prompt="hi", model_id=model.id
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="c")
        db.add(conv)
        await db.commit()
        return conv.id


@pytest.mark.asyncio
async def test_feedback_upsert_then_toggle(client: AsyncClient):
    conv_id = await _seed_conversation()
    msg_id = "msg-abc-123"

    # POST → up
    resp = await client.post(
        f"/api/messages/{msg_id}/feedback",
        json={"rating": "up", "conversation_id": str(conv_id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rating"] == "up"
    assert body["message_id"] == msg_id

    # POST → down (replace, same row)
    resp = await client.post(
        f"/api/messages/{msg_id}/feedback",
        json={"rating": "down", "conversation_id": str(conv_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == "down"

    # GET conversation feedback list returns the single rating
    resp = await client.get(f"/api/conversations/{conv_id}/feedback")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["rating"] == "down"

    # DELETE clears it
    resp = await client.delete(f"/api/messages/{msg_id}/feedback")
    assert resp.status_code == 204

    resp = await client.get(f"/api/conversations/{conv_id}/feedback")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_feedback_invalid_rating_rejected(client: AsyncClient):
    conv_id = await _seed_conversation()
    resp = await client.post(
        "/api/messages/m/feedback",
        json={"rating": "meh", "conversation_id": str(conv_id)},
    )
    assert resp.status_code == 422
