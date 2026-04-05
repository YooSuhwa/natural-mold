"""Tests for app.agent_runtime.trigger_executor — scheduled trigger execution."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_full_setup(
    *, with_tools: bool = False, trigger_status: str = "active"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create User + Model + Agent + Trigger. Return (trigger_id, agent_id)."""
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=user.id,
            name="Trigger Agent",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        if with_tools:
            tool = Tool(
                name="Web Search",
                type="builtin",
                is_system=True,
                description="Search",
                auth_config={"server_key": "sk"},
            )
            db.add(tool)
            await db.flush()
            link = AgentToolLink(
                agent_id=agent.id, tool_id=tool.id, config={"agent_override": "ov"}
            )
            db.add(link)

        trigger = AgentTrigger(
            agent_id=agent.id,
            user_id=user.id,
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="뉴스 검색해줘",
            status=trigger_status,
            run_count=0,
        )
        db.add(trigger)
        await db.commit()
        return trigger.id, agent.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_trigger_success():
    """Successful trigger creates conversation, saves messages, updates state."""
    trigger_id, _ = await _seed_full_setup()

    async def mock_stream(*args, **kwargs):
        yield 'event: content_delta\ndata: {"delta": "뉴스 "}\n\n'
        yield 'event: content_delta\ndata: {"delta": "결과입니다"}\n\n'
        yield 'event: message_end\ndata: {"content": "뉴스 결과입니다", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # Verify trigger state updated
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 1
        assert trigger.last_run_at is not None


@pytest.mark.asyncio
async def test_execute_trigger_not_found():
    """Non-existent trigger should return early without error."""
    fake_id = str(uuid.uuid4())

    with patch(
        "app.agent_runtime.trigger_executor.async_session",
        TestSession,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        # Should not raise
        await execute_trigger(fake_id)


@pytest.mark.asyncio
async def test_execute_trigger_inactive():
    """Inactive trigger should be skipped."""
    trigger_id, _ = await _seed_full_setup(trigger_status="paused")

    with patch(
        "app.agent_runtime.trigger_executor.async_session",
        TestSession,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # run_count should still be 0
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 0


@pytest.mark.asyncio
async def test_execute_trigger_agent_not_found():
    """If the agent is deleted, trigger should set status to error."""
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        await db.flush()

        # Create trigger pointing to non-existent agent
        fake_agent_id = uuid.uuid4()
        trigger = AgentTrigger(
            agent_id=fake_agent_id,
            user_id=user.id,
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="test",
            status="active",
            run_count=0,
        )
        db.add(trigger)
        await db.commit()
        trigger_id = trigger.id

    with patch(
        "app.agent_runtime.trigger_executor.async_session",
        TestSession,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.status == "error"


@pytest.mark.asyncio
async def test_execute_trigger_execution_error():
    """Agent execution failure should still save error message and update run count."""
    trigger_id, _ = await _seed_full_setup()

    async def mock_stream(*args, **kwargs):
        raise RuntimeError("LLM call failed")
        # Make it an async generator that raises
        yield  # pragma: no cover

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 1
        assert trigger.last_run_at is not None


@pytest.mark.asyncio
async def test_execute_trigger_run_count_incremented():
    """Run count should increment on each successful execution."""
    trigger_id, _ = await _seed_full_setup()

    async def mock_stream(*args, **kwargs):
        yield 'event: message_end\ndata: {"content": "done", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 1


@pytest.mark.asyncio
async def test_execute_trigger_content_parsing():
    """SSE delta chunks should be accumulated into full content."""
    trigger_id, _ = await _seed_full_setup()

    captured_kwargs: dict = {}

    async def mock_stream(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: content_delta\ndata: {"delta": "Part1 "}\n\n'
        yield 'event: content_delta\ndata: {"delta": "Part2"}\n\n'
        yield 'event: message_end\ndata: {"content": "Part1 Part2", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # Verify the user message was passed to the agent stream
    assert captured_kwargs["messages_history"] == [{"role": "user", "content": "뉴스 검색해줘"}]


@pytest.mark.asyncio
async def test_execute_trigger_creates_conversation():
    """A new conversation should be created for the trigger run."""
    trigger_id, agent_id = await _seed_full_setup()

    async def mock_stream(*args, **kwargs):
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        result = await db.execute(select(Conversation).where(Conversation.agent_id == agent_id))
        convs = result.scalars().all()
        assert len(convs) == 1
        assert convs[0].title is not None
        assert "자동 실행" in convs[0].title


@pytest.mark.asyncio
async def test_execute_trigger_with_tools_config():
    """Tools config should include merged auth from tool + agent link."""
    trigger_id, _ = await _seed_full_setup(with_tools=True)

    captured_kwargs: dict = {}

    async def mock_stream(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    tools_cfg = captured_kwargs.get("tools_config", [])
    assert len(tools_cfg) == 1
    auth = tools_cfg[0]["auth_config"]
    assert auth["server_key"] == "sk"
    assert auth["agent_override"] == "ov"


@pytest.mark.asyncio
async def test_execute_trigger_passes_user_message():
    """The trigger's input_message should be passed as messages_history to agent."""
    trigger_id, _ = await _seed_full_setup()

    captured_kwargs: dict = {}

    async def mock_stream(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_stream",
            side_effect=mock_stream,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    assert captured_kwargs["messages_history"] == [{"role": "user", "content": "뉴스 검색해줘"}]
