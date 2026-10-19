"""Actual m83 owned-lab concurrency preserves scoped pinned-summary serialization."""

import anyio
import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.authz.private_actor import PersistedActor
from app.config import settings
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from app.services.chat.conversations import create_conversation
from app.services.conversation_pinned_summary_service import (
    PinSummaryTarget,
    SummarySource,
    get_summary,
    pin_summary,
)
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_concurrent_first_pins_serialize_without_primary_key_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an independently migrated disposable database and actual private actor.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-pin-concurrency@example.com",
                "name": "Concurrency fixture",
                "password": "native-concurrency-password",
            },
        )
        assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            user = await db.scalar(
                select(User).where(User.email == "native-pin-concurrency@example.com")
            )
            assert user is not None and user.last_active_org_id is not None
            context = await resolve_org_context(
                db, PersistedActor(user.id, user.is_super_user), cookie=str(user.last_active_org_id)
            )
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.flush()
            agent = Agent(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Pin fixture",
                system_prompt="Fixture",
                model_id=model.id,
            )
            db.add(agent)
            await db.flush()
            conversation = await create_conversation(db, agent.id, context=context)
            conversation_id = conversation.id
            await db.commit()
        completed: list[str] = []

        async def pin(message_id: str) -> None:
            source = SummarySource(message_id, "assistant", f"Answer {message_id}", "checkpoint")
            async with stack.sessions() as db:
                await pin_summary(
                    db, PinSummaryTarget(conversation_id, context, message_id, (source,))
                )
                await db.commit()
                completed.append(message_id)

        # When independent transactions attempt the first pin concurrently.
        async with anyio.create_task_group() as group:
            group.start_soon(pin, "assistant-1")
            group.start_soon(pin, "assistant-2")
        # Then both writes commit without duplicate-child errors and one valid snapshot remains.
        assert sorted(completed) == ["assistant-1", "assistant-2"]
        async with stack.sessions() as db:
            row = await get_summary(db, conversation_id, context)
            assert row is not None and row.source_message_id in completed
            assert row.snapshot_text == f"Answer {row.source_message_id}"
