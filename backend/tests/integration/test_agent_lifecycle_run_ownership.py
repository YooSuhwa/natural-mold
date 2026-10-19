"""Native PostgreSQL proof for B08 durable execution ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_owner, conversation_run_service
from app.services.agent_execution_owner import ExecutionEnvelopeRef, OwnedExecutionRequest
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture

pytestmark = pytest.mark.integration


async def test_native_owner_rows_projection_and_foreign_ack_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        actors = fixture.actors
        context = OrgContext(
            user_id=actors.owner_one_id,
            org_id=actors.org_id,
            tenant_id=actors.tenant_id,
        )
        async with fixture.sessions() as db:
            seeded_root = await db.get(Agent, fixture.graph.root_agent_id)
            assert seeded_root is not None
            root = Agent(
                user_id=actors.owner_one_id,
                org_id=actors.org_id,
                tenant_id=actors.tenant_id,
                name="Task6 native root",
                system_prompt="root",
                model_id=seeded_root.model_id,
            )
            child = Agent(
                user_id=actors.owner_one_id,
                org_id=actors.org_id,
                tenant_id=actors.tenant_id,
                name="Task6 native child",
                system_prompt="child",
                model_id=seeded_root.model_id,
            )
            db.add_all([root, child])
            await db.flush()
            db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id, position=0))
            conversation = Conversation(
                user_id=actors.owner_one_id,
                org_id=actors.org_id,
                tenant_id=actors.tenant_id,
                agent_id=root.id,
                agent_name_snapshot=root.name,
                title="Task6 native",
                last_activity_at=datetime.now(UTC),
            )
            db.add(conversation)
            await db.flush()
            envelope = AgentApiRun(
                public_id=f"task6-{uuid4()}",
                user_id=actors.owner_one_id,
                deployment_id=None,
                deployment_public_id_snapshot="task6-native",
                conversation_id=conversation.id,
                mode="background",
                status="pending",
            )
            db.add(envelope)
            await db.flush()

            accepted = await agent_execution_owner.accept_owned_execution(
                db,
                OwnedExecutionRequest(
                    conversation_id=conversation.id,
                    root_agent_id=root.id,
                    context=context,
                    source="api",
                    input_preview="native",
                    envelope=ExecutionEnvelopeRef("api", envelope.id),
                ),
            )
            await db.commit()
            run_id = accepted.run_id
            root_id = root.id
            child_id = child.id
            envelope_id = envelope.id

        async with fixture.sessions() as owner_db:
            run = await owner_db.get(ConversationRun, run_id, with_for_update=True)
            assert run is not None
            await conversation_run_service.transition_run(
                owner_db, run, "running", worker_instance_id="native-owner"
            )
            await owner_db.commit()

        async with fixture.sessions() as cancel_db:
            run = await cancel_db.get(ConversationRun, run_id, with_for_update=True)
            assert run is not None
            await conversation_run_service.request_cancel_run(
                cancel_db, run, reason="agent_archived"
            )
            await cancel_db.commit()

        async with fixture.sessions() as foreign_db:
            run = await foreign_db.get(ConversationRun, run_id, with_for_update=True)
            assert run is not None
            with pytest.raises(ValueError, match="owning worker"):
                await conversation_run_service.transition_run(
                    foreign_db,
                    run,
                    "canceled",
                    cancellation_ack_worker_id="foreign-owner",
                )
            await foreign_db.rollback()

        async with fixture.sessions() as verify_db:
            run = await verify_db.get(ConversationRun, run_id)
            envelope = await verify_db.get(AgentApiRun, envelope_id)
            participants = list(
                (
                    await verify_db.scalars(
                        select(ConversationRunAgent)
                        .where(ConversationRunAgent.run_id == run_id)
                        .order_by(ConversationRunAgent.role)
                    )
                ).all()
            )
            assert run is not None and run.status == "canceling"
            assert run.worker_instance_id == "native-owner"
            assert run.cancellation_acknowledged_at is None
            assert envelope is not None and envelope.status == "canceling"
            assert [row.role for row in participants] == ["root", "subagent"]
            assert {row.agent_id_snapshot for row in participants} == {root_id, child_id}
            assert fixture.sentinel.provider_invocations == 0
