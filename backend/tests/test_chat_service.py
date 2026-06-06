"""Tests for app.services.chat_service — conversations, messages, token usage."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.schemas.conversation import MessageResponse
from app.services import chat_service, trace_storage
from app.services.chat_service import (
    create_conversation,
    get_agent_with_tools,
    get_conversation,
    list_conversations,
    maybe_set_auto_title,
    save_token_usage,
)
from tests.conftest import TEST_USER_ID, TestSession


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


@pytest.mark.asyncio
async def test_create_conversation_flushes_without_committing(db: AsyncSession):
    """Service mutation stays rollbackable by the router/request transaction."""
    agent_id = await _seed(db)
    await db.commit()

    conv = await create_conversation(db, agent_id)
    conv_id = conv.id
    await db.rollback()

    async with TestSession() as check_db:
        persisted = (
            await check_db.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one_or_none()
    assert persisted is None


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
# maybe_set_auto_title
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_title_from_first_user_message(db: AsyncSession):
    """First user message auto-generates conversation title from '새 대화'."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)  # title="새 대화"
    assert conv.title == "새 대화"

    await maybe_set_auto_title(db, conv.id, "오늘 날씨 어때?")

    updated = await get_conversation(db, conv.id)
    assert updated is not None
    assert updated.title == "오늘 날씨 어때?"


@pytest.mark.asyncio
async def test_auto_title_long_content_truncated(db: AsyncSession):
    """Long content is truncated to 37 chars + '...'."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    await maybe_set_auto_title(db, conv.id, "a" * 60)

    updated = await get_conversation(db, conv.id)
    assert updated is not None
    assert updated.title is not None
    assert len(updated.title) == 40
    assert updated.title.endswith("...")


@pytest.mark.asyncio
async def test_auto_title_no_change_when_already_set(db: AsyncSession):
    """Title is not overwritten if already set (not '새 대화')."""
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id, title="Custom Title")

    await maybe_set_auto_title(db, conv.id, "새로운 내용")

    updated = await get_conversation(db, conv.id)
    assert updated is not None
    assert updated.title == "Custom Title"  # unchanged


# ---------------------------------------------------------------------------
# save_token_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_token_usage(db: AsyncSession):
    agent_id = await _seed(db)
    await db.commit()
    conv = await create_conversation(db, agent_id)

    usage = await save_token_usage(
        db,
        conversation_id=conv.id,
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
# Pending HITL file-write approval hydration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydrates_pending_write_file_interrupt_from_trace_chunks(db: AsyncSession):
    """A paused DeepAgents write_file turn should render as approval, not a spinner."""
    agent_id = await _seed(db)
    conv = await create_conversation(db, agent_id, title="HITL file")
    msg_id = uuid.uuid4()
    raw_msg_id = str(msg_id)
    run_id = str(uuid.uuid4())
    interrupt_id = "agent:file-write"
    file_args = {
        "file_path": "/runtime/today_diary.md",
        "content": "# 오늘 하루\n\n좋은 하루였다.",
    }
    response = MessageResponse(
        id=msg_id,
        conversation_id=conv.id,
        role="assistant",
        content="파일을 만들게요.",
        tool_calls=[{"id": "toolu-1", "name": "write_file", "args": file_args}],
        tool_call_id=None,
        created_at=conv.created_at,
    )

    await trace_storage.append_events(
        db,
        conversation_id=conv.id,
        assistant_msg_id=run_id,
        events_chunk=[
            {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
            {
                "id": f"{run_id}-2",
                "event": "tool_call_start",
                "data": {
                    "tool_call_id": "toolu-1",
                    "tool_name": "write_file",
                    "parameters": {},
                },
            },
            {
                "id": f"{run_id}-3",
                "event": "interrupt",
                "data": {
                    "interrupt_id": interrupt_id,
                    "action_requests": [
                        {
                            "name": "write_file",
                            "args": file_args,
                            "description": "Tool execution requires approval",
                        }
                    ],
                    "review_configs": [
                        {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}
                    ],
                },
            },
            {
                "id": f"{run_id}-4",
                "event": "message_end",
                "data": {"content": "파일을 만들게요.", "status": "completed"},
            },
        ],
    )
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=run_id,
        raw_msg_ids=[raw_msg_id],
        conversation_id=conv.id,
    )
    await db.commit()

    await chat_service._hydrate_pending_interrupt_tool_calls(  # noqa: SLF001
        db,
        conversation_id=conv.id,
        responses=[response],
    )

    assert response.tool_calls == [
        {
            "id": "toolu-1",
            "name": "request_approval",
            "args": {
                "tool_name": "write_file",
                "tool_args": file_args,
                "description": "Tool execution requires approval",
                "approval_id": "toolu-1",
                "allowed_decisions": ["approve", "reject"],
                "hitl_interrupt_id": interrupt_id,
                "hitl_action_index": 0,
                "hitl_total_actions": 1,
            },
        }
    ]


@pytest.mark.asyncio
async def test_does_not_hydrate_completed_write_file_interrupt(db: AsyncSession):
    """Once write_file has a tool result, reload should not show a pending approval."""
    agent_id = await _seed(db)
    conv = await create_conversation(db, agent_id, title="Completed HITL file")
    assistant_msg_id = uuid.uuid4()
    raw_assistant_msg_id = str(assistant_msg_id)
    tool_result_msg_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    interrupt_id = "agent:file-write"
    file_args = {
        "file_path": "/conversations/thread-a/today_diary.md",
        "content": "# 오늘 하루\n\n좋은 하루였다.",
    }
    assistant_response = MessageResponse(
        id=assistant_msg_id,
        conversation_id=conv.id,
        role="assistant",
        content="",
        tool_calls=[{"id": "toolu-1", "name": "write_file", "args": file_args}],
        tool_call_id=None,
        created_at=conv.created_at,
    )
    tool_response = MessageResponse(
        id=tool_result_msg_id,
        conversation_id=conv.id,
        role="tool",
        content="Updated file /conversations/thread-a/today_diary.md",
        tool_calls=None,
        tool_call_id="toolu-1",
        created_at=conv.created_at,
    )

    await trace_storage.append_events(
        db,
        conversation_id=conv.id,
        assistant_msg_id=run_id,
        events_chunk=[
            {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
            {
                "id": f"{run_id}-2",
                "event": "interrupt",
                "data": {
                    "interrupt_id": interrupt_id,
                    "action_requests": [
                        {
                            "name": "write_file",
                            "args": file_args,
                            "description": "Tool execution requires approval",
                        }
                    ],
                    "review_configs": [
                        {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}
                    ],
                },
            },
        ],
    )
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=run_id,
        raw_msg_ids=[raw_assistant_msg_id],
        conversation_id=conv.id,
    )
    await db.commit()

    await chat_service._hydrate_pending_interrupt_tool_calls(  # noqa: SLF001
        db,
        conversation_id=conv.id,
        responses=[assistant_response, tool_response],
    )

    assert assistant_response.tool_calls == [
        {"id": "toolu-1", "name": "write_file", "args": file_args}
    ]


@pytest.mark.asyncio
async def test_hydrates_interrupt_only_on_matching_tool_call_response(db: AsyncSession):
    """A write_file interrupt should not be appended to unrelated tool-call messages."""
    agent_id = await _seed(db)
    conv = await create_conversation(db, agent_id, title="Scoped HITL file")
    datetime_msg_id = uuid.uuid4()
    write_msg_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    file_args = {
        "file_path": "/conversations/thread-a/today_diary.md",
        "content": "# 오늘 하루\n\n좋은 하루였다.",
    }
    datetime_response = MessageResponse(
        id=datetime_msg_id,
        conversation_id=conv.id,
        role="assistant",
        content="오늘 날짜를 확인할게요.",
        tool_calls=[{"id": "toolu-date", "name": "current_datetime", "args": {}}],
        tool_call_id=None,
        created_at=conv.created_at,
    )
    write_response = MessageResponse(
        id=write_msg_id,
        conversation_id=conv.id,
        role="assistant",
        content="",
        tool_calls=[{"id": "toolu-write", "name": "write_file", "args": file_args}],
        tool_call_id=None,
        created_at=conv.created_at,
    )

    await trace_storage.append_events(
        db,
        conversation_id=conv.id,
        assistant_msg_id=run_id,
        events_chunk=[
            {
                "id": f"{run_id}-1",
                "event": "interrupt",
                "data": {
                    "interrupt_id": "agent:file-write",
                    "action_requests": [
                        {
                            "name": "write_file",
                            "args": file_args,
                            "description": "Tool execution requires approval",
                        }
                    ],
                    "review_configs": [
                        {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}
                    ],
                },
            },
        ],
    )
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=run_id,
        raw_msg_ids=[str(datetime_msg_id), str(write_msg_id)],
        conversation_id=conv.id,
    )
    await db.commit()

    await chat_service._hydrate_pending_interrupt_tool_calls(  # noqa: SLF001
        db,
        conversation_id=conv.id,
        responses=[datetime_response, write_response],
    )

    assert datetime_response.tool_calls == [
        {"id": "toolu-date", "name": "current_datetime", "args": {}}
    ]
    assert write_response.tool_calls == [
        {
            "id": "toolu-write",
            "name": "request_approval",
            "args": {
                "tool_name": "write_file",
                "tool_args": file_args,
                "description": "Tool execution requires approval",
                "approval_id": "toolu-write",
                "allowed_decisions": ["approve", "reject"],
                "hitl_interrupt_id": "agent:file-write",
                "hitl_action_index": 0,
                "hitl_total_actions": 1,
            },
        }
    ]


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
        definition_key="builtin:web_search",
        description="Search the web",
    )
    db.add(tool)
    await db.flush()

    link = AgentToolLink(agent_id=agent.id, tool_id=tool.id)
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
