from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.main import create_app
from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.share_link import ShareLink
from app.models.user import User
from app.services import conversation_run_service
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture


async def test_native_archive_and_restore_preserve_history_and_stop_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the migrated full graph plus unrelated child-only execution.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        async with fixture.sessions() as db:
            owner = await db.get(User, fixture.actors.owner_one_id)
            child = await db.get(Agent, graph.child_agent_ids[0])
            assert owner is not None and child is not None
            owner_email = owner.email
            unrelated_conversation = Conversation(
                id=uuid4(),
                tenant_id=fixture.actors.tenant_id,
                org_id=fixture.actors.org_id,
                agent_id=child.id,
                user_id=fixture.actors.owner_two_id,
                agent_name_snapshot=child.name,
                agent_lifecycle_state="active",
                last_activity_at=datetime.now(UTC).replace(tzinfo=None),
                title="Unrelated child standalone",
                source="ui",
            )
            db.add(unrelated_conversation)
            await db.flush()
            unrelated_run = ConversationRun(
                id=uuid4(),
                conversation_id=unrelated_conversation.id,
                agent_id=child.id,
                user_id=fixture.actors.owner_two_id,
                source="chat",
                status="running",
                is_active=True,
                worker_instance_id="foreign-worker",
            )
            db.add(unrelated_run)
            await db.flush()
            db.add(
                ConversationRunAgent(
                    run_id=unrelated_run.id,
                    agent_id=child.id,
                    agent_id_snapshot=child.id,
                    role="root",
                    path=[],
                )
            )
            pending = ConversationRunInput(
                id=uuid4(),
                conversation_id=graph.conversation_ids[0],
                agent_id=graph.root_agent_id,
                user_id=fixture.actors.owner_one_id,
                run_id=None,
                client_request_id=f"archive-pending-{uuid4()}",
                source="ui",
                status="pending",
                priority=0,
                position=2,
                revision=1,
                input_payload={"message": "pending"},
                attachment_ids=[],
            )
            db.add(pending)
            await db.commit()
            pending_id = pending.id
            unrelated_run_id = unrelated_run.id

        app = create_app()

        async def database_session() -> AsyncGenerator[AsyncSession, None]:
            async with fixture.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database_session

        # When: authenticated HTTP archive passes the real cookie and CSRF guards.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"email": owner_email, "password": "fixture password 42"},
            )
            assert login.status_code == 200, login.text
            archived = await client.delete(
                f"/api/agents/{graph.root_agent_id}",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
            )
            assert archived.status_code == 204, archived.text
            archived_list = await client.get("/api/agents/archived")
            assert archived_list.status_code == 200, archived_list.text
            metadata = next(
                item
                for item in archived_list.json()
                if item["agent_id"] == str(graph.root_agent_id)
            )
            assert metadata["lifecycle_state"] == "archived_stopping"

        # Then: only participant runs are canceling and retained bindings remain truthful.
        async with fixture.sessions() as db:
            root = await db.get(Agent, graph.root_agent_id)
            conversations = tuple(
                await db.scalars(
                    select(Conversation).where(Conversation.id.in_(graph.conversation_ids))
                )
            )
            runs = tuple(
                await db.scalars(
                    select(ConversationRun)
                    .where(ConversationRun.id.in_(graph.run_ids))
                    .order_by(ConversationRun.id)
                )
            )
            claimed = tuple(
                await db.scalars(
                    select(ConversationRunInput).where(ConversationRunInput.id.in_(graph.input_ids))
                )
            )
            stored_pending = await db.get(ConversationRunInput, pending_id)
            stored_unrelated = await db.get(ConversationRun, unrelated_run_id)
            share = await db.get(ShareLink, graph.share_link_id)
            trigger = await db.scalar(
                select(AgentTrigger).where(AgentTrigger.agent_id == graph.root_agent_id)
            )
            deployment = await db.scalar(
                select(AgentDeployment).where(AgentDeployment.agent_id == graph.root_agent_id)
            )
            links = tuple(
                await db.scalars(
                    select(AgentSubAgentLink).where(
                        AgentSubAgentLink.parent_agent_id == graph.root_agent_id
                    )
                )
            )
            assert root is not None and root.archived_at is not None
            assert all(item.agent_lifecycle_state == "archived" for item in conversations)
            assert all(run.status == "canceling" for run in runs)
            assert all(item.status == "claimed" and item.run_id is not None for item in claimed)
            assert stored_pending is not None
            assert (stored_pending.status, stored_pending.run_id, stored_pending.revision) == (
                "canceled",
                None,
                2,
            )
            assert stored_unrelated is not None and stored_unrelated.status == "running"
            assert share is not None and share.revoked_at is not None
            assert trigger is not None and trigger.status == "paused"
            assert deployment is not None and deployment.status == "disabled"
            assert links == ()

            for run in runs:
                await conversation_run_service.transition_run(
                    db,
                    run,
                    "canceled",
                    cancellation_ack_worker_id=run.worker_instance_id,
                    allow_workerless_cancellation_ack=run.worker_instance_id is None,
                )
            await db.commit()

        # When: the owner restores through real auth after every durable owner ACK.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"email": owner_email, "password": "fixture password 42"},
            )
            assert login.status_code == 200, login.text
            restored = await client.post(
                f"/api/agents/{graph.root_agent_id}/restore",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["lifecycle_state"] == "active"

        # Then: restoration never recreates revoked entry points or invokes a provider.
        async with fixture.sessions() as db:
            share = await db.get(ShareLink, graph.share_link_id)
            trigger = await db.scalar(
                select(AgentTrigger).where(AgentTrigger.agent_id == graph.root_agent_id)
            )
            deployment = await db.scalar(
                select(AgentDeployment).where(AgentDeployment.agent_id == graph.root_agent_id)
            )
            assert share is not None and share.revoked_at is not None
            assert trigger is not None and trigger.status == "paused"
            assert deployment is not None and deployment.status == "disabled"
            assert fixture.sentinel.provider_invocations == 0
