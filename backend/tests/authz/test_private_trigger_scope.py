"""Scheduled runtime checks persisted scope and current authority even for forced runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import OrganizationMember, Tenant
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.schemas.trigger import TriggerCreate
from app.services import agent_service, trigger_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.fixture
async def private_trigger(
    db: AsyncSession, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> AgentTrigger:
    monkeypatch.setattr(trigger_service, "sync_scheduler_for_trigger", AsyncMock())
    monkeypatch.setattr(trigger_executor, "async_session", TestSession)
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    agent = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Scheduled fixture",
            system_prompt="Fixture",
            model_id=model.id,
            identity_mode="fixed",
        ),
        private_scopes.first,
    )
    return await trigger_service.create_trigger(
        db,
        agent.id,
        private_scopes.first,
        TriggerCreate(
            trigger_type="interval",
            schedule_config={"interval_minutes": 15},
            input_message="Fixture",
        ),
    )


async def test_schedule_conversation_writer_preserves_trigger_scope(
    db: AsyncSession, private_trigger: AgentTrigger, private_scopes: PrivateScopes
) -> None:
    assert (private_trigger.user_id, private_trigger.org_id, private_trigger.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
    conversation = await trigger_service.resolve_schedule_conversation(
        db, private_trigger, private_scopes.first
    )
    assert (conversation.user_id, conversation.org_id, conversation.tenant_id) == (
        private_trigger.user_id,
        private_trigger.org_id,
        private_trigger.tenant_id,
    )
    reused = await trigger_service.resolve_schedule_conversation(
        db, private_trigger, private_scopes.first
    )
    assert reused.id == conversation.id
    assert await trigger_service.get_trigger(db, private_trigger.id, private_scopes.second) is None
    with pytest.raises(AppError) as captured:
        await trigger_service.resolve_schedule_conversation(
            db, private_trigger, private_scopes.second
        )
    assert captured.value.status == 404


@pytest.mark.parametrize("change", ["membership", "company", "account"])
async def test_forced_trigger_cannot_bypass_revoked_private_actor(
    db: AsyncSession,
    private_trigger: AgentTrigger,
    change: Literal["membership", "company", "account"],
) -> None:
    match change:
        case "membership":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == private_trigger.org_id,
                    OrganizationMember.user_id == private_trigger.user_id,
                )
            )
        case "company":
            await db.execute(
                update(Tenant)
                .where(Tenant.id == private_trigger.tenant_id)
                .values(status="suspended")
            )
        case "account":
            await db.execute(
                update(User).where(User.id == private_trigger.user_id).values(is_active=False)
            )
    await db.commit()
    run = await trigger_executor.execute_trigger(str(private_trigger.id), force=True)
    assert run is None
    assert await db.scalar(select(AgentTriggerRun.id)) is None
    await db.refresh(private_trigger)
    assert private_trigger.status == "active"
    assert private_trigger.failure_count == 0


@pytest.mark.parametrize("foreign_parent", ["agent", "conversation"])
async def test_trigger_references_do_not_expose_foreign_private_content(
    db: AsyncSession,
    private_trigger: AgentTrigger,
    private_scopes: PrivateScopes,
    foreign_parent: Literal["agent", "conversation"],
) -> None:
    context = private_scopes.second
    other_agent = Agent(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Other organization",
        system_prompt="Fixture",
        model_id=await db.scalar(
            select(Agent.model_id).where(Agent.id == private_trigger.agent_id)
        ),
    )
    db.add(other_agent)
    await db.flush()
    other_conversation = Conversation(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=other_agent.id,
        title="Private foreign title",
        unread_count=17,
        agent_name_snapshot=other_agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(other_conversation)
    await db.flush()
    match foreign_parent:
        case "agent":
            private_trigger.agent_id = other_agent.id
        case "conversation":
            private_trigger.schedule_conversation_id = other_conversation.id
    await db.commit()
    rows = await trigger_service.list_user_triggers(db, private_scopes.first)
    if foreign_parent == "agent":
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0]["schedule_conversation_title"] is None
        assert rows[0]["schedule_conversation_unread_count"] == 0
    # Rejected execution must not leave an orphan running claim or change the trigger.
    run = await trigger_executor.execute_trigger(str(private_trigger.id), force=True)
    assert run is None
    assert await db.scalar(select(AgentTriggerRun.id)) is None
    await db.refresh(private_trigger)
    assert private_trigger.status == "active"
