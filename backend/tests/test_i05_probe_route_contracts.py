"""Route contracts the I-05 local rehearsal probes flagged (plan 8.4, 16.6).

- ``GET /api/conversations/{id}/feedback`` answered 200 ``[]`` for another user's (or a
  nonexistent) conversation instead of the private-resource 404.
- ``PUT``/``DELETE /api/conversations/{id}/pinned-summary`` ran the ownership gate before
  CSRF, so a request without the CSRF header got 404 instead of 403 ``csrf_mismatch``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from tests.conftest import TestSession
from tests.test_multiuser_isolation import _apply, _make_agent, _register, _seed_default_model


async def _conversation_of(client: AsyncClient, email: str):
    model_id = await _seed_default_model()
    owner = await _register(client, email=email)
    agent_id = await _make_agent(client, owner, model_id)
    async with TestSession() as db:
        agent = await db.get(Agent, uuid.UUID(agent_id))
        assert agent is not None
        conv = Conversation(
            id=uuid.uuid4(),
            agent_id=agent.id,
            title="t",
            org_id=agent.org_id,
            tenant_id=agent.tenant_id,
            user_id=agent.user_id,
            agent_name_snapshot="Fixture agent",
            last_activity_at=datetime.now(UTC),
        )
        db.add(conv)
        await db.commit()
        return owner, str(conv.id)


@pytest.mark.asyncio
async def test_feedback_list_hides_foreign_and_unknown_conversations(raw_client: AsyncClient):
    owner, conv_id = await _conversation_of(raw_client, "fb-a@test.com")
    other = await _register(raw_client, email="fb-b@test.com")

    _apply(raw_client, owner)
    own = await raw_client.get(f"/api/conversations/{conv_id}/feedback")
    assert own.status_code == 200 and own.json() == []

    _apply(raw_client, other)
    for target in (conv_id, str(uuid.uuid4())):
        resp = await raw_client.get(f"/api/conversations/{target}/feedback")
        assert resp.status_code == 404, target
        assert resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_pinned_summary_mutations_check_csrf_before_ownership(raw_client: AsyncClient):
    owner, conv_id = await _conversation_of(raw_client, "pin-a@test.com")
    other = await _register(raw_client, email="pin-b@test.com")
    _apply(raw_client, other)
    path = f"/api/conversations/{conv_id}/pinned-summary"
    unknown = f"/api/conversations/{uuid.uuid4()}/pinned-summary"
    body = {"message_id": "m-1"}
    for url in (path, unknown):
        put = await raw_client.put(url, json=body)
        delete = await raw_client.delete(url)
        for resp in (put, delete):
            assert resp.status_code == 403, (url, resp.text)
            assert resp.json()["error"]["code"] == "csrf_mismatch"
    # With CSRF the ownership gate still hides the foreign conversation.
    put = await raw_client.put(path, json=body, headers=other.headers())
    delete = await raw_client.delete(path, headers=other.headers())
    assert (put.status_code, delete.status_code) == (404, 404)
    _apply(raw_client, owner)
    assert (await raw_client.delete(path, headers=owner.headers())).status_code == 204
