"""Actual m83 direct execution claims recheck persisted scope and membership."""

from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.authz.conversation_run_scope import RunConversationTarget, RunExecutionTarget
from app.authz.conversation_scope import resolve_conversation_actor
from app.config import settings
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services import conversation_run_service as runs
from app.services import conversation_run_worker as worker
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("revoked", [False, True])
async def test_native_direct_worker_claim_rechecks_membership(
    monkeypatch: pytest.MonkeyPatch, revoked: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        # Given a queued run on the actual m83 schema with a captured execution scope.
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-direct-worker@example.com",
                "name": "Worker fixture",
                "password": "native-worker-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Worker fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        conversation = await stack.client.post(
            f"/api/agents/{agent.json()['id']}/conversations",
            headers=headers,
            json={"title": "Worker fixture"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = UUID(conversation.json()["id"])
        async with stack.sessions() as db:
            user = await db.scalar(
                select(User).where(User.email == "native-direct-worker@example.com")
            )
            assert user is not None
            context = await resolve_conversation_actor(db, conversation_id, user.id)
            run = await runs.create_run(
                db,
                conversation_id=conversation_id,
                agent_id=UUID(agent.json()["id"]),
                context=context,
                source="chat",
                input_preview="Native fixture",
            )
            target = RunExecutionTarget(
                run.id, RunConversationTarget(conversation_id, run.agent_id, context)
            )
            if revoked:
                await db.execute(
                    update(OrganizationMember)
                    .where(
                        OrganizationMember.org_id == context.org_id,
                        OrganizationMember.user_id == context.user_id,
                    )
                    .values(status="suspended")
                )
            await db.commit()
        monkeypatch.setattr(worker, "async_session", stack.sessions)

        # When a detached worker attempts the actual running transition.
        loaded, started = await worker._transition_to_running(target, worker_instance_id="native")

        # Then only still-authorized work becomes running, without a provider call.
        assert started is not revoked
        async with stack.sessions() as db:
            persisted = await db.get(ConversationRun, target.run_id)
            assert persisted is not None
            if revoked:
                assert loaded is None and persisted.status == "queued"
                assert persisted.worker_instance_id is None
            else:
                assert loaded is not None and loaded.id == target.run_id
                assert persisted.status == "running" and persisted.worker_instance_id == "native"
