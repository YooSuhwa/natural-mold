"""Lifecycle and durable-ownership contracts for scheduled Agent triggers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.schemas.trigger import TriggerRunResponse
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_trigger(*, archived: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="trigger-lifecycle@test.com", name="Owner")
        db.add(user)
        model = Model(provider="openai", model_name="trigger-model", display_name="Trigger")
        db.add(model)
        await db.flush()
        await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
        agent = Agent(
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name="Lifecycle Trigger",
            system_prompt="run",
            model_id=model.id,
            archived_at=datetime.now(UTC) if archived else None,
            archived_by=user.id if archived else None,
        )
        db.add(agent)
        await db.flush()
        trigger = AgentTrigger(
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_id=agent.id,
            name="Lifecycle schedule",
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="run now",
            status="paused" if archived else "active",
        )
        db.add(trigger)
        await db.commit()
        return trigger.id, agent.id


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_scheduled_and_forced_runs_share_one_durable_owner(force: bool) -> None:
    trigger_id, agent_id = await _seed_trigger()

    with (
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", return_value="done"),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        envelope = await execute_trigger(str(trigger_id), force=force)

    assert envelope is not None
    async with TestSession() as db:
        stored = await db.get(AgentTriggerRun, envelope.id)
        assert stored is not None and stored.conversation_run_id is not None
        durable = await db.get(ConversationRun, stored.conversation_run_id)
        assert durable is not None and durable.status == "completed"
        participants = list(
            await db.scalars(
                select(ConversationRunAgent).where(ConversationRunAgent.run_id == durable.id)
            )
        )
        assert [(item.agent_id_snapshot, item.role) for item in participants] == [
            (agent_id, "root")
        ]
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ConversationRun)
                .where(ConversationRun.conversation_id == stored.conversation_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_force_archived_agent_never_invokes_or_creates_run() -> None:
    trigger_id, _agent_id = await _seed_trigger(archived=True)

    with (
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke", return_value="bad"
        ) as invoke,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        result = await execute_trigger(str(trigger_id), force=True)

    assert result is None
    invoke.assert_not_called()
    async with TestSession() as db:
        assert await db.scalar(select(func.count()).select_from(AgentTriggerRun)) == 0
        assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 0


@pytest.mark.asyncio
async def test_stale_legacy_trigger_run_remains_an_unacknowledged_claim() -> None:
    trigger_id, agent_id = await _seed_trigger()
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        stale = AgentTriggerRun(
            trigger_id=trigger.id,
            agent_id=agent_id,
            trigger_name_snapshot=trigger.name,
            user_id=TEST_USER_ID,
            input_message="legacy owner unknown",
            status="running",
            started_at=datetime(2020, 1, 1),
        )
        db.add(stale)
        await db.commit()
        stale_id = stale.id

    with (
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke", return_value="bad"
        ) as invoke,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        result = await execute_trigger(str(trigger_id))

    assert result is None
    invoke.assert_not_called()
    async with TestSession() as db:
        rows = list(await db.scalars(select(AgentTriggerRun)))
        assert [row.id for row in rows] == [stale_id]
        assert rows[0].status == "running"
        assert rows[0].finished_at is None


@pytest.mark.asyncio
async def test_detached_trigger_run_serializes_from_immutable_snapshot() -> None:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="retained-trigger@test.com", name="Owner")
        db.add(user)
        retained = AgentTriggerRun(
            trigger_id=None,
            agent_id=None,
            trigger_name_snapshot="Retained trigger name",
            user_id=user.id,
            input_message="retained input",
            status="success",
            source="scheduled",
        )
        db.add(retained)
        await db.commit()
        await db.refresh(retained)

        payload = TriggerRunResponse.model_validate(retained)

    assert payload.trigger_id is None
    assert payload.agent_id is None
    assert payload.trigger_name_snapshot == "Retained trigger name"
