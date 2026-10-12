"""Core Agent, execution, API, and trigger rows for the B08 fixture."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentApiThread, AgentDeployment
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.model import Model
from tests.authz.agent_lifecycle_fixture import LifecycleActors, LifecycleGraph


async def seed_core_rows(
    db: AsyncSession,
    actors: LifecycleActors,
    graph: LifecycleGraph,
) -> None:
    """Persist the owned root/child tree and three execution envelopes."""
    now = datetime.now(UTC).replace(tzinfo=None)
    model_id = uuid4()
    suffix = graph.root_agent_id.hex[:12]
    db.add(
        Model(
            id=model_id,
            provider="openai_compatible",
            model_name=f"fixture-model-{suffix}",
            display_name="Fixture model",
            base_url="http://127.0.0.1.invalid",
            is_default=False,
            is_visible=False,
            default_credential_id=None,
            source="manual",
        )
    )
    await db.flush()
    agent_specs = (
        (graph.root_agent_id, actors.owner_one_id, "Lifecycle root", f"b08-root-{suffix}"),
        (
            graph.child_agent_ids[0],
            actors.owner_two_id,
            "Lifecycle child A",
            f"b08-child-a-{suffix}",
        ),
        (
            graph.child_agent_ids[1],
            actors.owner_two_id,
            "Lifecycle child B",
            f"b08-child-b-{suffix}",
        ),
    )
    for agent_id, owner_id, name, runtime_name in agent_specs:
        db.add(
            Agent(
                id=agent_id,
                tenant_id=actors.tenant_id,
                org_id=actors.org_id,
                user_id=owner_id,
                name=name,
                description="B08 fixture",
                system_prompt="fixture",
                runtime_name=runtime_name,
                identity_mode="fixed",
                model_id=model_id,
                llm_credential_id=None,
                status="active",
                runtime_profile="standard",
                is_favorite=False,
            )
        )
    await db.flush()
    for position, child_id in enumerate(graph.child_agent_ids):
        db.add(
            AgentSubAgentLink(
                parent_agent_id=graph.root_agent_id,
                sub_agent_id=child_id,
                position=position,
            )
        )
    await db.flush()
    sources = ("ui", "api", "trigger")
    for index, conversation_id in enumerate(graph.conversation_ids):
        db.add(
            Conversation(
                id=conversation_id,
                tenant_id=actors.tenant_id,
                org_id=actors.org_id,
                agent_id=graph.root_agent_id,
                user_id=actors.owner_one_id if index != 1 else actors.owner_two_id,
                agent_name_snapshot="Lifecycle root",
                agent_lifecycle_state="active",
                last_activity_at=now,
                title=f"B08 {sources[index]}",
                source=sources[index],
                is_pinned=index == 0,
                active_branch_checkpoint_id=graph.checkpoint_ids[-1] if index == 0 else None,
            )
        )
    await db.flush()
    statuses = ("queued", "running", "canceling")
    run_sources = ("chat", "start", "resume")
    for index, run_id in enumerate(graph.run_ids):
        db.add(
            ConversationRun(
                id=run_id,
                conversation_id=graph.conversation_ids[index],
                agent_id=graph.root_agent_id,
                user_id=actors.owner_one_id if index != 1 else actors.owner_two_id,
                source=run_sources[index],
                status=statuses[index],
                is_active=True,
                worker_instance_id="fixture-worker" if index else None,
                input_preview=f"fixture-{sources[index]}",
                started_at=now if index else None,
                heartbeat_at=now if index else None,
                cancel_requested_at=now if index == 2 else None,
                cancel_reason="agent_archived" if index == 2 else None,
            )
        )
    await db.flush()
    for index, run_id in enumerate(graph.run_ids):
        participants = (graph.root_agent_id, *graph.child_agent_ids)
        for participant_index, participant_id in enumerate(participants):
            db.add(
                ConversationRunAgent(
                    run_id=run_id,
                    agent_id=participant_id,
                    agent_id_snapshot=participant_id,
                    role="root" if participant_index == 0 else "subagent",
                    path=[] if participant_index == 0 else [participant_index - 1],
                )
            )
        db.add(
            ConversationRunInput(
                id=graph.input_ids[index],
                conversation_id=graph.conversation_ids[index],
                agent_id=graph.root_agent_id,
                user_id=actors.owner_one_id if index != 1 else actors.owner_two_id,
                run_id=run_id,
                client_request_id=f"b08-{sources[index]}",
                source=sources[index],
                status="claimed",
                priority=0,
                position=1,
                revision=1,
                input_payload={"message": f"fixture-{sources[index]}"},
                attachment_ids=[],
                claimed_at=now,
            )
        )
    deployment_id = uuid4()
    public_id = f"b08-lifecycle-{suffix}"
    db.add(
        AgentDeployment(
            id=deployment_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            agent_id=graph.root_agent_id,
            user_id=actors.owner_one_id,
            public_id=public_id,
            status="active",
            allow_streaming=True,
            allow_background=True,
        )
    )
    await db.flush()
    api_thread_id = uuid4()
    db.add(
        AgentApiThread(
            id=api_thread_id,
            public_id=f"b08-thread-{suffix}",
            user_id=actors.owner_two_id,
            deployment_id=deployment_id,
            deployment_public_id_snapshot=public_id,
            conversation_id=graph.conversation_ids[1],
            external_user="fixture-external",
            meta={"static": True},
        )
    )
    await db.flush()
    db.add(
        AgentApiRun(
            id=graph.api_run_id,
            public_id=f"b08-api-run-{suffix}",
            user_id=actors.owner_two_id,
            api_key_id=None,
            deployment_id=deployment_id,
            deployment_public_id_snapshot=public_id,
            conversation_run_id=graph.run_ids[1],
            thread_id=api_thread_id,
            conversation_id=graph.conversation_ids[1],
            mode="background",
            status="running",
            input={"message": "fixture-api"},
            output=None,
            meta={"static": True},
            started_at=now,
        )
    )
    trigger_id = uuid4()
    db.add(
        AgentTrigger(
            id=trigger_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            agent_id=graph.root_agent_id,
            user_id=actors.owner_one_id,
            name="B08 lifecycle trigger",
            trigger_type="interval",
            schedule_config={"minutes": 5},
            input_message="fixture-trigger",
            timezone="Asia/Seoul",
            conversation_policy="schedule_thread",
            target_conversation_id=graph.conversation_ids[2],
            status="active",
        )
    )
    await db.flush()
    db.add(
        AgentTriggerRun(
            id=graph.trigger_run_id,
            trigger_id=trigger_id,
            agent_id=graph.root_agent_id,
            trigger_name_snapshot="B08 lifecycle trigger",
            conversation_run_id=graph.run_ids[2],
            user_id=actors.owner_one_id,
            conversation_id=graph.conversation_ids[2],
            status="canceling",
            source="scheduled",
            input_message="fixture-trigger",
            thread_id=str(graph.conversation_ids[2]),
            credential_subject_user_id=actors.owner_one_id,
        )
    )
    await db.commit()


__all__ = ["seed_core_rows"]
