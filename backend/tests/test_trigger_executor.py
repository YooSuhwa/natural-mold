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
    *,
    with_tools: bool = False,
    trigger_status: str = "active",
    tool_definition_key: str = "builtin:web_search",
    tool_name: str = "Web Search",
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
                name=tool_name,
                definition_key=tool_definition_key,
                description=tool_name,
            )
            db.add(tool)
            await db.flush()
            link = AgentToolLink(agent_id=agent.id, tool_id=tool.id)
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
    """Successful trigger creates conversation, updates state."""
    trigger_id, _ = await _seed_full_setup()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="뉴스 결과입니다",
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
        from app.models.agent_trigger_run import AgentTriggerRun

        result = await db.execute(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
        )
        run = result.scalar_one()
        assert run.source == "scheduled"
        assert run.duration_ms is not None
        assert run.duration_ms >= 0
        assert run.thread_id == str(run.conversation_id)
        assert run.output_preview == "뉴스 결과입니다"


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
        from sqlalchemy import select

        from app.models.agent_trigger_run import AgentTriggerRun

        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 0
        assert trigger.last_status == "skipped"
        result = await db.execute(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
        )
        run = result.scalar_one()
        assert run.status == "skipped"


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
    """Agent execution failure records a failed run without success-counting it."""
    trigger_id, _ = await _seed_full_setup()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            side_effect=RuntimeError("LLM call failed"),
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
        assert trigger.run_count == 0
        assert trigger.last_run_at is not None
        assert trigger.last_status == "failed"
        assert "LLM call failed" in (trigger.last_error or "")


@pytest.mark.asyncio
async def test_execute_trigger_run_count_incremented():
    """Run count should increment on each successful execution."""
    trigger_id, _ = await _seed_full_setup()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="done",
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
async def test_execute_trigger_run_now_records_source():
    """Forced runs should be marked as run_now in run history."""
    trigger_id, _ = await _seed_full_setup()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="manual result",
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id), force=True)

    async with TestSession() as db:
        from app.models.agent_trigger_run import AgentTriggerRun

        result = await db.execute(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
        )
        run = result.scalar_one()
        assert run.source == "run_now"
        assert run.output_preview == "manual result"


@pytest.mark.asyncio
async def test_execute_trigger_completes_when_max_runs_reached():
    """A successful run that reaches max_runs should complete the trigger."""
    trigger_id, _ = await _seed_full_setup()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.max_runs = 1
        await db.commit()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="done",
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
        assert trigger.status == "completed"
        assert trigger.next_run_at is None


@pytest.mark.asyncio
async def test_execute_trigger_skips_when_max_runs_already_reached():
    """A trigger past max_runs should not invoke the agent again."""
    trigger_id, _ = await _seed_full_setup()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.max_runs = 1
        trigger.run_count = 1
        await db.commit()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="done",
        ) as mock_invoke,
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    mock_invoke.assert_not_called()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.status == "completed"
        assert trigger.last_status == "skipped"


@pytest.mark.asyncio
async def test_execute_trigger_auto_pauses_after_failure_threshold():
    """Repeated failures should pause the trigger when configured."""
    trigger_id, _ = await _seed_full_setup()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.auto_pause_after_failures = 1
        await db.commit()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            side_effect=RuntimeError("provider down"),
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
        assert trigger.failure_count == 1
        assert trigger.status == "paused"
        assert "provider down" in (trigger.last_error or "")


@pytest.mark.asyncio
async def test_execute_trigger_passes_messages():
    """User message should be passed to execute_agent_invoke."""
    trigger_id, _ = await _seed_full_setup()

    captured_args: list = []

    async def mock_invoke(*args, **kwargs):
        captured_args.extend(args)
        return "Part1 Part2"

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            side_effect=mock_invoke,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # args: (cfg: AgentConfig, messages_history: list)
    assert captured_args[0].provider_api_keys == {"openai": "test-api-key"}
    assert captured_args[1] == [{"role": "user", "content": "뉴스 검색해줘"}]


@pytest.mark.asyncio
async def test_execute_trigger_creates_conversation():
    """A schedule-thread conversation should be created for the trigger run."""
    trigger_id, agent_id = await _seed_full_setup()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="ok",
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
        assert "스케줄:" in convs[0].title
        assert convs[0].unread_count == 1
        assert convs[0].last_activity_source == "schedule"


@pytest.mark.asyncio
async def test_execute_trigger_new_per_run_creates_a_new_conversation_each_time():
    """new_per_run policy should not reuse the previous schedule conversation."""
    trigger_id, agent_id = await _seed_full_setup()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.conversation_policy = "new_per_run"
        await db.commit()

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="ok",
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))
        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        result = await db.execute(select(Conversation).where(Conversation.agent_id == agent_id))
        convs = result.scalars().all()
        assert len(convs) == 2
        assert all(conv.unread_count == 1 for conv in convs)


@pytest.mark.asyncio
async def test_execute_trigger_selected_conversation_uses_target_conversation():
    """selected_conversation policy should write into the configured conversation."""
    trigger_id, agent_id = await _seed_full_setup()
    async with TestSession() as db:
        target = Conversation(agent_id=agent_id, title="기존 대화")
        db.add(target)
        await db.flush()
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.conversation_policy = "selected_conversation"
        trigger.target_conversation_id = target.id
        await db.commit()
        target_id = target.id

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="ok",
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    async with TestSession() as db:
        target = await db.get(Conversation, target_id)
        assert target is not None
        assert target.unread_count == 1
        result = await db.execute(select(Conversation).where(Conversation.agent_id == agent_id))
        assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_execute_trigger_with_tools_config():
    """Greenfield tools_config carries definition_key + decrypted credentials."""
    trigger_id, _ = await _seed_full_setup(with_tools=True)

    captured_args: list = []

    async def mock_invoke(*args, **kwargs):
        captured_args.extend(args)
        return "ok"

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            side_effect=mock_invoke,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # args[0] is AgentConfig — check tools_config on it
    cfg = captured_args[0]
    assert len(cfg.tools_config) == 1
    entry = cfg.tools_config[0]
    assert entry["definition_key"] == "builtin:web_search"
    assert entry["credentials"] is None
    assert entry["credential_id"] is None


@pytest.mark.asyncio
async def test_execute_trigger_blocks_external_mutation_tool():
    """Scheduled runs must not auto-execute tools that require live approval."""
    trigger_id, _ = await _seed_full_setup(
        with_tools=True,
        tool_definition_key="gmail_send",
        tool_name="Gmail Send",
    )

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="should not run",
        ) as mock_invoke,
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    mock_invoke.assert_not_called()
    async with TestSession() as db:
        from app.models.agent_trigger_run import AgentTriggerRun

        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        assert trigger.run_count == 0
        assert trigger.last_status == "failed"
        assert "tool risk policy" in (trigger.last_error or "")
        assert "Gmail Send" in (trigger.last_error or "")

        result = await db.execute(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
        )
        run = result.scalar_one()
        assert run.status == "failed"
        assert "Gmail Send" in (run.error_message or "")


@pytest.mark.asyncio
async def test_execute_trigger_passes_user_message():
    """The trigger's input_message should be passed as messages_history to agent."""
    trigger_id, _ = await _seed_full_setup()

    captured_args: list = []

    async def mock_invoke(*args, **kwargs):
        captured_args.extend(args)
        return "ok"

    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            side_effect=mock_invoke,
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    # args: (cfg: AgentConfig, messages_history: list)
    assert captured_args[1] == [{"role": "user", "content": "뉴스 검색해줘"}]
