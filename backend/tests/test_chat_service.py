"""Tests for app.services.chat_service — conversations, messages, token usage."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.services.chat_service import (
    create_conversation,
    get_agent_with_tools,
    get_conversation,
    list_conversations,
    list_messages,
    save_message,
    save_token_usage,
)
from tests.conftest import TEST_USER_ID


async def _seed(db: AsyncSession) -> uuid.UUID:
    """Create User + Model + Agent, return agent_id."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Chat Agent",
        system_prompt="Hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    return agent.id


# ---------------------------------------------------------------------------
# list_conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_empty(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()

    convs = await list_conversations(db, agent_id)
    assert convs == []


@pytest.mark.asyncio
async def test_list_conversations_with_data(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()

    await create_conversation(db, agent_id, title="First")
    await create_conversation(db, agent_id, title="Second")

    convs = await list_conversations(db, agent_id)
    assert len(convs) == 2
    # Most recent first (order by updated_at desc)
    assert convs[0].title == "Second"
    assert convs[1].title == "First"


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_default_title(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()

    conv = await create_conversation(db, agent_id)
    assert conv.title == "새 대화"
    assert conv.agent_id == agent_id
    assert conv.id is not None


@pytest.mark.asyncio
async def test_create_conversation_custom_title(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()

    conv = await create_conversation(db, agent_id, title="Custom Title")
    assert conv.title == "Custom Title"


# ---------------------------------------------------------------------------
# get_conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation_found(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id, title="Find Me")

    found = await get_conversation(db, conv.id)
    assert found is not None
    assert found.title == "Find Me"


@pytest.mark.asyncio
async def test_get_conversation_not_found(db: AsyncSession):
    result = await get_conversation(db, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_ordering(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    await save_message(db, conv.id, "user", "Hello")
    await save_message(db, conv.id, "assistant", "Hi there!")
    await save_message(db, conv.id, "user", "How are you?")

    msgs = await list_messages(db, conv.id)
    assert len(msgs) == 3
    # Chronological order (oldest first)
    assert msgs[0].content == "Hello"
    assert msgs[1].content == "Hi there!"
    assert msgs[2].content == "How are you?"


@pytest.mark.asyncio
async def test_list_messages_limit(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    for i in range(5):
        await save_message(db, conv.id, "user", f"Message {i}")

    msgs = await list_messages(db, conv.id, limit=3)
    assert len(msgs) == 3


# ---------------------------------------------------------------------------
# save_message — title auto-generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_message_user_generates_title(db: AsyncSession):
    """First user message auto-generates conversation title from '새 대화'."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)  # title="새 대화"
    assert conv.title == "새 대화"

    await save_message(db, conv.id, "user", "오늘 날씨 어때?")

    updated = await get_conversation(db, conv.id)
    assert updated.title == "오늘 날씨 어때?"


@pytest.mark.asyncio
async def test_save_message_user_long_title_truncated(db: AsyncSession):
    """Long user messages are truncated to 37 chars + '...'."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    long_msg = "a" * 60
    await save_message(db, conv.id, "user", long_msg)

    updated = await get_conversation(db, conv.id)
    assert len(updated.title) == 40
    assert updated.title.endswith("...")


@pytest.mark.asyncio
async def test_save_message_assistant_no_title_change(db: AsyncSession):
    """Assistant messages should NOT trigger title change."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    await save_message(db, conv.id, "assistant", "안녕하세요!")

    updated = await get_conversation(db, conv.id)
    assert updated.title == "새 대화"  # unchanged


# ---------------------------------------------------------------------------
# save_token_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_token_usage(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)
    msg = await save_message(db, conv.id, "assistant", "Hello!")

    usage = await save_token_usage(
        db,
        message_id=msg.id,
        agent_id=agent_id,
        model_name="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated_cost=0.005,
    )
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150
    assert usage.id is not None


# ---------------------------------------------------------------------------
# get_agent_with_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_with_tools_found(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Tooled Agent",
        system_prompt="Hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    tool = Tool(
        name="Web Search",
        type="builtin",
        is_system=True,
        description="Search the web",
    )
    db.add(tool)
    await db.flush()

    link = AgentToolLink(agent_id=agent.id, tool_id=tool.id, config=None)
    db.add(link)
    await db.commit()

    result = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert result is not None
    assert result.name == "Tooled Agent"
    assert result.model is not None
    assert len(result.tool_links) == 1
    assert result.tool_links[0].tool.name == "Web Search"


@pytest.mark.asyncio
async def test_get_agent_with_tools_not_found(db: AsyncSession):
    result = await get_agent_with_tools(db, uuid.uuid4(), TEST_USER_ID)
    assert result is None
