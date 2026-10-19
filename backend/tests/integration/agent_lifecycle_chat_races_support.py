from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_worker, conversation_run_service
from app.services.chat.conversations import create_conversation
from tests.authz.agent_lifecycle_fixture import (
    RegisteredLifecycleFixture,
    registered_lifecycle_fixture,
)
from tests.authz.agent_lifecycle_revoked_fixture import run_revoked_owner_maintenance_history
from tests.authz.queue_resource_fixture import queue_resource_sql


async def _seed_same_owner_chat(
    fixture: RegisteredLifecycleFixture,
) -> tuple[OrgContext, UUID, UUID, UUID]:
    actors = fixture.actors
    context = OrgContext(actors.org_id, actors.tenant_id, actors.owner_two_id)
    async with fixture.sessions() as db:
        seeded = await db.get(Agent, fixture.graph.root_agent_id)
        assert seeded is not None
        root = Agent(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name=f"Task9 root {uuid4().hex}",
            system_prompt="root",
            model_id=seeded.model_id,
        )
        child = Agent(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name=f"Task9 child {uuid4().hex}",
            system_prompt="child",
            model_id=seeded.model_id,
        )
        db.add_all([root, child])
        await db.flush()
        db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id))
        await db.commit()
        conversation = await create_conversation(db, root.id, "Task9 chat", context=context)
        draft = await create_conversation(
            db, root.id, "Task9 draft", context=context, source="draft"
        )
        await db.commit()
        return context, root.id, conversation.id, draft.id


async def assert_native_revoked_inactive_recovery_preserves_history_without_payload_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        now = datetime.now(UTC)
        conversation_ids: list[UUID] = []
        run_ids: list[UUID] = []
        input_ids: list[UUID] = []
        async with fixture.sessions() as db:
            for user_id, worker_id in (
                (fixture.actors.owner_one_id, None),
                (fixture.actors.inactive_admin_id, None),
                (fixture.actors.owner_one_id, "remote-owner"),
            ):
                conversation_id = uuid4()
                run_id = uuid4()
                input_id = uuid4()
                conversation = Conversation(
                    id=conversation_id,
                    user_id=user_id,
                    org_id=fixture.actors.org_id,
                    tenant_id=fixture.actors.tenant_id,
                    agent_id=None,
                    agent_name_snapshot="Purged",
                    agent_lifecycle_state="purged",
                    agent_purged_at=now,
                    title="Task9 recovery",
                    last_activity_at=now,
                )
                run = ConversationRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    agent_id=None,
                    user_id=user_id,
                    source="chat",
                    status="queued",
                    is_active=True,
                    worker_instance_id=worker_id,
                )
                queued = ConversationRunInput(
                    id=input_id,
                    conversation_id=conversation_id,
                    agent_id=None,
                    user_id=user_id,
                    run_id=run_id,
                    client_request_id=f"task9-{run_id}",
                    source="chat",
                    status="claimed",
                    position=1,
                    input_payload={"private": "must-not-hydrate"},
                    attachment_ids=[],
                    revision=1,
                )
                db.add_all((conversation, run, queued))
                conversation_ids.append(conversation_id)
                run_ids.append(run_id)
                input_ids.append(input_id)
            await db.commit()

        proof = await run_revoked_owner_maintenance_history(fixture)
        monkeypatch.setattr(conversation_run_queue_worker, "async_session", fixture.sessions)
        async with fixture.sessions() as db:
            for run_id in run_ids:
                run = await db.get(ConversationRun, run_id)
                assert run is not None
                await conversation_run_service.request_cancel_run(db, run, reason="agent_archived")
            await db.commit()

        launch_attempts: list[UUID] = []

        async def observe_recovery_scan(input_id: UUID) -> ConversationRun | None:
            launch_attempts.append(input_id)
            return None

        monkeypatch.setattr(
            conversation_run_queue_worker,
            "launch_claimed_input",
            observe_recovery_scan,
        )
        async with fixture.sessions() as observer_db:
            with queue_resource_sql(observer_db) as statements:
                recovery_count = await conversation_run_queue_worker.recover_conversation_queue()
        async with fixture.sessions() as db:
            runs = [await db.get(ConversationRun, run_id) for run_id in run_ids]
            inputs = [await db.get(ConversationRunInput, input_id) for input_id in input_ids]
        owner_run, inactive_run, remote_run = runs
        owner_input, inactive_input, remote_input = inputs
        assert recovery_count == 0
        assert not set(input_ids).intersection(launch_attempts)
        assert owner_run is not None
        assert owner_run.status == "canceled"
        assert owner_run.cancellation_acknowledged_at is not None
        assert inactive_run is not None
        assert inactive_run.status == "canceled"
        assert inactive_run.cancellation_acknowledged_at is not None
        assert remote_run is not None
        assert remote_run.status == "canceling"
        assert remote_run.cancellation_acknowledged_at is None
        assert owner_input is not None
        assert (owner_input.status, owner_input.run_id) == ("canceled", None)
        assert owner_input.revision == 2
        assert inactive_input is not None
        assert (inactive_input.status, inactive_input.run_id) == ("canceled", None)
        assert inactive_input.revision == 2
        assert remote_input is not None
        assert (remote_input.status, remote_input.run_id) == ("claimed", remote_run.id)
        assert remote_input.revision == 1
        assert not any("input_payload" in statement.lower() for statement in statements)
        assert proof.revoked_ack
        assert proof.inactive_ack
        assert proof.remote_ack_absent
        assert proof.retained_history_masked
        assert proof.revoked_denial == (403, "forbidden")
