from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.conversation import Conversation
from tests.conftest import TestSession
from tests.test_conversations_router import _seed_agent


@pytest.mark.asyncio
async def test_side_chat_is_separate_owned_and_hidden_until_saved(client: AsyncClient) -> None:
    agent_id, _ = await _seed_agent()
    response = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "Main"})
    parent_id = response.json()["id"]
    created = await client.post(f"/api/conversations/{parent_id}/side-chats")
    assert created.status_code == 201
    side_id = created.json()["id"]
    assert side_id != parent_id
    assert created.json()["agent_id"] == str(agent_id)
    async with TestSession() as db:
        side = await db.get(Conversation, uuid.UUID(side_id))
        assert side is not None and side.source == "side_chat"
        parent = await db.get(Conversation, uuid.UUID(parent_id))
        assert parent is not None and parent.runtime_policy_snapshot is not None
        assert side.runtime_policy_snapshot == parent.runtime_policy_snapshot
        assert side.runtime_policy_hash == parent.runtime_policy_hash
        assert side.side_chat_parent_id == parent.id
    reopened = await client.post(f"/api/conversations/{parent_id}/side-chats")
    assert reopened.status_code == 200
    assert reopened.json()["id"] == side_id
    listed = await client.get(f"/api/agents/{agent_id}/conversations")
    assert side_id not in {row["id"] for row in listed.json()}
    saved = await client.post(f"/api/conversations/{side_id}/side-chats/save")
    assert saved.status_code == 200
    listed = await client.get(f"/api/agents/{agent_id}/conversations")
    assert side_id in {row["id"] for row in listed.json()}
    assert (await client.post(f"/api/conversations/{side_id}/side-chats/save")).status_code == 200
    assert (await client.post(f"/api/conversations/{parent_id}/side-chats")).json()["id"] == side_id


@pytest.mark.asyncio
async def test_side_chat_mutations_require_real_csrf(raw_client: AsyncClient) -> None:
    from tests.conftest import register_session
    from tests.test_multiuser_isolation import _seed_default_model

    session = await register_session(raw_client, email="side-csrf@test.com", name="Side")
    headers = session.headers()
    agent = await raw_client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Side",
            "system_prompt": "Help",
            "model_id": await _seed_default_model(),
        },
    )
    assert agent.status_code == 201
    parent = await raw_client.post(
        f"/api/agents/{agent.json()['id']}/conversations", headers=headers, json={}
    )
    parent_id = parent.json()["id"]
    assert (await raw_client.post(f"/api/conversations/{parent_id}/side-chats")).status_code == 403
    side = await raw_client.post(f"/api/conversations/{parent_id}/side-chats", headers=headers)
    assert side.status_code == 201
    side_id = side.json()["id"]
    assert (
        await raw_client.post(f"/api/conversations/{side_id}/side-chats/save")
    ).status_code == 403
    assert (
        await raw_client.post(f"/api/conversations/{side_id}/side-chats/save", headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_deleting_parent_keeps_side_history_discoverable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.agent_runtime.checkpointer.delete_thread", AsyncMock())
    agent_id, _ = await _seed_agent()
    parent = await client.post(f"/api/agents/{agent_id}/conversations", json={})
    parent_id = parent.json()["id"]
    side = await client.post(f"/api/conversations/{parent_id}/side-chats")
    side_id = side.json()["id"]
    deleted = await client.delete(f"/api/conversations/{parent_id}")
    assert deleted.status_code == 204
    listed = await client.get(f"/api/agents/{agent_id}/conversations")
    assert side_id in {row["id"] for row in listed.json()}


@pytest.mark.asyncio
async def test_side_chat_routes_hide_foreign_and_missing_conversations(client: AsyncClient) -> None:
    agent_id, _ = await _seed_agent(user_id=uuid.uuid4(), user_email="foreign-side@test.com")
    async with TestSession() as db:
        conversation = Conversation(agent_id=agent_id, title="Private", source="side_chat")
        db.add(conversation)
        await db.commit()
        foreign_id = conversation.id
    for conversation_id in (foreign_id, uuid.uuid4()):
        for suffix in ("side-chats", "side-chats/save"):
            result = await client.post(f"/api/conversations/{conversation_id}/{suffix}")
            assert result.status_code == 404


@pytest.mark.asyncio
async def test_side_chat_cross_user_cookie_sessions_hide_foreign_resources(
    raw_client: AsyncClient,
) -> None:
    from tests.conftest import register_session
    from tests.test_multiuser_isolation import _make_agent, _seed_default_model

    owner = await register_session(raw_client, email="side-owner@test.com", name="Owner")
    other = await register_session(raw_client, email="side-other@test.com", name="Other")
    agent_id = await _make_agent(raw_client, owner, await _seed_default_model())
    parent = await raw_client.post(
        f"/api/agents/{agent_id}/conversations", headers=owner.headers(), json={}
    )
    assert parent.status_code == 201
    parent_id = parent.json()["id"]
    side = await raw_client.post(
        f"/api/conversations/{parent_id}/side-chats", headers=owner.headers()
    )
    assert side.status_code == 201
    side_id = side.json()["id"]

    other.apply(raw_client)
    for conversation_id in (parent_id, side_id, str(uuid.uuid4())):
        for suffix in ("side-chats", "side-chats/save"):
            response = await raw_client.post(
                f"/api/conversations/{conversation_id}/{suffix}", headers=other.headers()
            )
            assert response.status_code == 404

    owner.apply(raw_client)
    reopened = await raw_client.post(
        f"/api/conversations/{parent_id}/side-chats", headers=owner.headers()
    )
    assert reopened.status_code == 200
    assert reopened.json()["id"] == side_id
