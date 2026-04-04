"""Tests for app.routers.conversations — conversation CRUD and message streaming."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation, Message
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_agent(*, with_tools: bool = False) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Create User + Model + Agent (+ optionally a tool link). Return (agent_id, tool_id)."""
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=user.id,
            name="Conv Agent",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        tool_id = None
        if with_tools:
            tool = Tool(
                name="Web Search",
                type="builtin",
                is_system=True,
                description="Search the web",
                auth_config={"server_key": "val"},
            )
            db.add(tool)
            await db.flush()
            link = AgentToolLink(agent_id=agent.id, tool_id=tool.id, config={"extra": "cfg"})
            db.add(link)
            tool_id = tool.id

        await db.commit()
        return agent.id, tool_id


async def _seed_conversation(agent_id: uuid.UUID) -> uuid.UUID:
    async with TestSession() as db:
        conv = Conversation(agent_id=agent_id, title="Test Conv")
        db.add(conv)
        await db.commit()
        return conv.id


async def _seed_message(conv_id: uuid.UUID, role: str, content: str) -> uuid.UUID:
    async with TestSession() as db:
        msg = Message(conversation_id=conv_id, role=role, content=content)
        db.add(msg)
        await db.commit()
        return msg.id


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_empty(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    resp = await client.get(f"/api/agents/{agent_id}/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_conversations_with_data(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    await _seed_conversation(agent_id)
    await _seed_conversation(agent_id)

    resp = await client.get(f"/api/agents/{agent_id}/conversations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_conversations_agent_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/api/agents/{fake_id}/conversations")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    resp = await client.post(
        f"/api/agents/{agent_id}/conversations",
        json={"title": "New Chat"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "New Chat"
    assert data["agent_id"] == str(agent_id)


@pytest.mark.asyncio
async def test_create_conversation_default_title(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    resp = await client.post(
        f"/api/agents/{agent_id}/conversations",
        json={},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "새 대화"


@pytest.mark.asyncio
async def test_create_conversation_agent_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(f"/api/agents/{fake_id}/conversations", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/conversations/{conversation_id}/messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_empty(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_messages_with_data(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)
    await _seed_message(conv_id, "user", "Hello")
    await _seed_message(conv_id, "assistant", "Hi!")

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_list_messages_conversation_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/api/conversations/{fake_id}/messages")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/conversations/{conversation_id}/messages — streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_streaming(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    async def mock_stream(*args, **kwargs):
        yield 'event: message_start\ndata: {"id": "test-msg", "role": "assistant"}\n\n'
        yield 'event: content_delta\ndata: {"delta": "Hello"}\n\n'
        yield 'event: message_end\ndata: {"content": "Hello", "usage": {}}\n\n'

    with patch("app.routers.conversations.execute_agent_stream", side_effect=mock_stream):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "Hi there"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "message_start" in body
    assert "content_delta" in body
    assert "message_end" in body


@pytest.mark.asyncio
async def test_send_message_saves_user_message(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    async def mock_stream(*args, **kwargs):
        yield 'event: message_end\ndata: {"content": "Reply", "usage": {}}\n\n'

    with patch("app.routers.conversations.execute_agent_stream", side_effect=mock_stream):
        await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "User says hello"},
        )

    # Verify user message was saved
    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    msgs = resp.json()
    assert any(m["role"] == "user" and m["content"] == "User says hello" for m in msgs)


@pytest.mark.asyncio
async def test_send_message_conversation_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(
        f"/api/conversations/{fake_id}/messages",
        json={"content": "Hello"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_message_with_tools_merges_auth_config(client: AsyncClient):
    """Agent with tools should merge tool.auth_config + agent_tools.config."""
    agent_id, _ = await _seed_agent(with_tools=True)
    conv_id = await _seed_conversation(agent_id)

    captured_kwargs: dict = {}

    async def mock_stream(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: message_end\ndata: {"content": "Done", "usage": {}}\n\n'

    with patch("app.routers.conversations.execute_agent_stream", side_effect=mock_stream):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "Test"},
        )

    assert resp.status_code == 200
    # Verify tools_config was built with merged auth
    tools_cfg = captured_kwargs.get("tools_config", [])
    assert len(tools_cfg) == 1
    auth = tools_cfg[0]["auth_config"]
    # merged: tool.auth_config {"server_key": "val"} + link.config {"extra": "cfg"}
    assert auth["server_key"] == "val"
    assert auth["extra"] == "cfg"


# ---------------------------------------------------------------------------
# PATCH /api/conversations/{conversation_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_conversation_rename(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    resp = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "New Name"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "New Name"


@pytest.mark.asyncio
async def test_update_conversation_pin(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    resp = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"is_pinned": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_pinned"] is True


@pytest.mark.asyncio
async def test_update_conversation_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.patch(
        f"/api/conversations/{fake_id}",
        json={"title": "Ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/conversations/{conversation_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    conv_id = await _seed_conversation(agent_id)

    resp = await client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 204

    # Verify conversation no longer appears in listing
    resp = await client.get(f"/api/agents/{agent_id}/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_conversation_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.delete(f"/api/conversations/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/conversations — pinned sort order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_pinned_first(client: AsyncClient):
    agent_id, _ = await _seed_agent()
    await _seed_conversation(agent_id)  # conv A (unpinned)
    conv_b = await _seed_conversation(agent_id)  # conv B — will pin
    await _seed_conversation(agent_id)  # conv C (unpinned)

    # Pin conv B
    resp = await client.patch(
        f"/api/conversations/{conv_b}",
        json={"is_pinned": True},
    )
    assert resp.status_code == 200

    # List should return pinned conversation first
    resp = await client.get(f"/api/agents/{agent_id}/conversations")
    assert resp.status_code == 200
    convs = resp.json()
    assert len(convs) == 3
    assert convs[0]["id"] == str(conv_b)
    assert convs[0]["is_pinned"] is True
