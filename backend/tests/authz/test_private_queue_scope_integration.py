"""Real m83 queue claiming and revoked-actor cancellation follow persisted scope."""

from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.authz.conversation_scope import resolve_conversation_actor
from app.config import settings
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_service
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_queue_claim_and_system_cancellation_after_user_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-queue@example.com",
                "name": "Queue fixture",
                "password": "native-queue-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        created = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Queue fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert created.status_code == 201, created.text
        conversation = await stack.client.post(
            f"/api/agents/{created.json()['id']}/conversations",
            headers=headers,
            json={"title": "Queue fixture"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = UUID(conversation.json()["id"])
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-queue@example.com"))
            assert user is not None
            context = await resolve_conversation_actor(db, conversation_id, user.id)
            queued = await queue.enqueue_input(
                db,
                context=context,
                conversation_id=conversation_id,
                client_request_id="native-queue",
                source="chat",
                input_payload={"messages": [{"role": "user", "content": "Fixture"}]},
                attachment_ids=[],
                checkpoint_id=None,
                priority=0,
            )
            assert queued.user_id == user.id
            await db.commit()
            claimed = await queue.claim_next_input(
                db, conversation_id=conversation_id, context=context
            )
            assert claimed is not None
            run_id = claimed.run.id
            assert claimed.run.conversation_id == conversation_id and claimed.run.user_id == user.id
            await conversation_run_service.request_cancel_run(db, claimed.run, reason="stop")
            await db.execute(update(User).where(User.id == user.id).values(is_active=False))
            await db.commit()
        # System cancellation must still stop work when private execution authority is revoked.
        finalized = await finalize_workerless_cancel_before_start(
            session_factory=stack.sessions,
            protected_run_ids=(),
        )
        assert finalized == [conversation_id]
        async with stack.sessions() as db:
            run = await db.get(ConversationRun, run_id)
            assert run is not None and run.status == "canceled" and run.is_active is False
