"""Persisted trigger runtime setup for native lifecycle races."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import get_db
from app.main import create_app
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture


@dataclass(frozen=True, slots=True)
class RemoteOwnedTrigger:
    run_id: UUID
    conversation_id: UUID
    context: OrgContext


async def seed_trigger(
    fixture: RegisteredLifecycleFixture,
    *,
    label: str,
) -> tuple[UUID, UUID]:
    actors = fixture.actors
    async with fixture.sessions() as db:
        model = Model(
            provider="openai_compatible",
            model_name=f"task11-{label}-{uuid4().hex}",
            display_name=f"Task 11 {label}",
            base_url="https://example.invalid/v1",
            is_visible=True,
            source="manual",
        )
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=actors.owner_one_id,
            org_id=actors.org_id,
            tenant_id=actors.tenant_id,
            name=f"Task 11 {label}",
            system_prompt="native trigger",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        trigger = AgentTrigger(
            agent_id=agent.id,
            user_id=actors.owner_one_id,
            org_id=actors.org_id,
            tenant_id=actors.tenant_id,
            name=f"Task 11 {label}",
            trigger_type="interval",
            schedule_config={"minutes": 30},
            input_message="native trigger",
            timezone="Asia/Seoul",
            conversation_policy="new_per_run",
            status="active",
        )
        db.add(trigger)
        await db.commit()
        return agent.id, trigger.id


async def archive_client(
    fixture: RegisteredLifecycleFixture,
) -> tuple[AsyncClient, str]:
    async with fixture.sessions() as db:
        owner = await db.get(User, fixture.actors.owner_one_id)
        assert owner is not None
        email = owner.email
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with fixture.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture"), email


async def prepare_remote_trigger_run(
    fixture: RegisteredLifecycleFixture,
    *,
    agent_id: UUID,
    trigger_id: UUID,
) -> RemoteOwnedTrigger:
    actors = fixture.actors
    context = OrgContext(
        org_id=actors.org_id,
        tenant_id=actors.tenant_id,
        user_id=actors.owner_one_id,
    )
    async with fixture.sessions() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        agent = await db.get(Agent, agent_id)
        assert trigger is not None and agent is not None
        conversation = Conversation(
            user_id=actors.owner_one_id,
            org_id=actors.org_id,
            tenant_id=actors.tenant_id,
            agent_id=agent.id,
            agent_name_snapshot=agent.name,
            title="Task 11 remote owner",
            last_activity_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(conversation)
        await db.flush()
        envelope = AgentTriggerRun(
            trigger_id=trigger.id,
            agent_id=agent.id,
            trigger_name_snapshot=trigger.name,
            user_id=actors.owner_one_id,
            conversation_id=conversation.id,
            status="queued",
            source="scheduled",
            input_message="remote",
        )
        db.add(envelope)
        await db.flush()
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=agent.id,
                context=context,
                source="trigger",
                input_preview="remote",
                envelope=ExecutionEnvelopeRef("trigger", envelope.id),
            ),
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run is not None
        await conversation_run_service.transition_run(
            db, run, "running", worker_instance_id="remote-native-owner"
        )
        await db.commit()
        return RemoteOwnedTrigger(run.id, conversation.id, context)


__all__ = [
    "RemoteOwnedTrigger",
    "archive_client",
    "prepare_remote_trigger_run",
    "seed_trigger",
]
