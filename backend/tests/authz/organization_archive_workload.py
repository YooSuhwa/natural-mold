"""Seed one organization's live workload for organization-archive tests (internal I22)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.model import Model

MARKER_DIGEST = "a" * 64


@dataclass(frozen=True, slots=True)
class Workload:
    agent_ids: tuple[UUID, ...]
    agent_id: UUID
    idle_agent_id: UUID
    trigger_id: UUID
    deployment_id: UUID
    pending_input_id: UUID
    pending_conversation_id: UUID
    api_run_id: UUID
    api_input_id: UUID
    api_conversation_run_id: UUID
    trigger_run_id: UUID
    trigger_input_id: UUID
    trigger_conversation_run_id: UUID
    lease_run_id: UUID
    lease_input_id: UUID
    lease_api_run_id: UUID
    conversation_ids: tuple[UUID, ...]
    done_conversation_id: UUID
    done_run_id: UUID
    done_input_id: UUID
    unlinked_api_run_id: UUID
    unlinked_trigger_run_id: UUID


def _conversation(agent: Agent, title: str) -> Conversation:
    return Conversation(
        agent_id=agent.id,
        user_id=agent.user_id,
        org_id=agent.org_id,
        tenant_id=agent.tenant_id,
        title=title,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )


def _run(conversation: Conversation, status: str, *, marker: bool = False) -> ConversationRun:
    return ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        # API/trigger runs store the canonical "start" source (ck_conversation_runs_source);
        # the entry kind lives on the envelope rows seeded below.
        source="start",
        status=status,
        is_active=True,
        runtime_materialized_at=datetime.now(UTC) if marker else None,
        runtime_materialization_sha256=MARKER_DIGEST if marker else None,
    )


def _input(conversation: Conversation, run: ConversationRun | None) -> ConversationRunInput:
    return ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        run_id=None if run is None else run.id,
        client_request_id=uuid4().hex,
        source="chat",
        status="pending" if run is None else "claimed",
        position=1,
        input_payload={"text": "hello"},
    )


def _api_run(deployment: AgentDeployment, run: ConversationRun | None) -> AgentApiRun:
    return AgentApiRun(
        public_id=f"run_{uuid4().hex}",
        user_id=deployment.user_id,
        deployment_id=deployment.id,
        deployment_public_id_snapshot=deployment.public_id,
        conversation_run_id=None if run is None else run.id,
        conversation_id=None if run is None else run.conversation_id,
        mode="background",
        status="running" if run is not None else "pending",
    )


async def seed_workload(
    db: AsyncSession, *, tenant_id: UUID, org_id: UUID, user_id: UUID
) -> Workload:
    model = Model(provider="openai", model_name=f"i22-{uuid4().hex}", display_name="I22")
    db.add(model)
    await db.flush()
    agents = [
        Agent(
            user_id=user_id,
            org_id=org_id,
            tenant_id=tenant_id,
            name=f"Archive agent {index}",
            system_prompt="prompt",
            model_id=model.id,
        )
        for index in range(2)
    ]
    db.add_all(agents)
    await db.flush()
    agent = agents[0]
    trigger = AgentTrigger(
        agent_id=agent.id,
        user_id=user_id,
        org_id=org_id,
        tenant_id=tenant_id,
        trigger_type="interval",
        schedule_config={"minutes": 5},
        input_message="tick",
        next_run_at=datetime.now(UTC).replace(tzinfo=None),
    )
    deployment = AgentDeployment(
        agent_id=agent.id,
        user_id=user_id,
        org_id=org_id,
        tenant_id=tenant_id,
        public_id=f"dep_{uuid4().hex}",
    )
    conversations = [_conversation(agent, f"c{index}") for index in range(4)]
    done_conv = _conversation(agent, "done")
    db.add_all((trigger, deployment, *conversations, done_conv))
    await db.flush()
    pending_conv, api_conv, trigger_conv, lease_conv = conversations
    api_run = _run(api_conv, "queued")
    trigger_run = _run(trigger_conv, "running")
    lease_run = _run(lease_conv, "running", marker=True)
    done_run = _run(done_conv, "completed")
    done_run.is_active, done_run.completed_at = False, datetime.now(UTC).replace(tzinfo=None)
    db.add_all((api_run, trigger_run, lease_run, done_run))
    await db.flush()
    done_input = _input(done_conv, done_run)
    pending_input = _input(pending_conv, None)
    api_input = _input(api_conv, api_run)
    trigger_input = _input(trigger_conv, trigger_run)
    lease_input = _input(lease_conv, lease_run)
    api_envelope = _api_run(deployment, api_run)
    lease_envelope = _api_run(deployment, lease_run)
    trigger_envelope = AgentTriggerRun(
        trigger_id=trigger.id,
        agent_id=agent.id,
        trigger_name_snapshot=trigger.name,
        conversation_run_id=trigger_run.id,
        user_id=user_id,
        conversation_id=trigger_conv.id,
        status="running",
        input_message="tick",
    )
    unlinked_api = _api_run(deployment, None)
    unlinked_trigger = AgentTriggerRun(
        trigger_id=trigger.id,
        agent_id=agent.id,
        trigger_name_snapshot=trigger.name,
        user_id=user_id,
        status="running",
        input_message="tick",
    )
    db.add_all(
        (
            done_input,
            unlinked_api,
            unlinked_trigger,
            pending_input,
            api_input,
            trigger_input,
            lease_input,
            api_envelope,
            lease_envelope,
            trigger_envelope,
        )
    )
    await db.commit()
    return Workload(
        agent_ids=tuple(sorted((item.id for item in agents), key=lambda value: value.bytes)),
        agent_id=agent.id,
        idle_agent_id=agents[1].id,
        trigger_id=trigger.id,
        deployment_id=deployment.id,
        pending_input_id=pending_input.id,
        pending_conversation_id=pending_conv.id,
        api_run_id=api_envelope.id,
        api_input_id=api_input.id,
        api_conversation_run_id=api_run.id,
        trigger_run_id=trigger_envelope.id,
        trigger_input_id=trigger_input.id,
        trigger_conversation_run_id=trigger_run.id,
        lease_run_id=lease_run.id,
        lease_input_id=lease_input.id,
        lease_api_run_id=lease_envelope.id,
        conversation_ids=tuple(
            sorted((item.id for item in conversations), key=lambda value: value.bytes)
        ),
        done_conversation_id=done_conv.id,
        done_run_id=done_run.id,
        done_input_id=done_input.id,
        unlinked_api_run_id=unlinked_api.id,
        unlinked_trigger_run_id=unlinked_trigger.id,
    )
