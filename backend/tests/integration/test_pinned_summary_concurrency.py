from __future__ import annotations

import os
import uuid

import anyio
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_pinned_summary import ConversationPinnedSummary
from app.models.model import Model
from app.models.user import User
from app.services.conversation_pinned_summary_service import SummarySource, pin_summary

pytestmark = pytest.mark.integration


def _postgres_session_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not raw_url:
        pytest.skip("INTEGRATION_DATABASE_URL is required")
    parsed = make_url(raw_url)
    if not (parsed.database or "").startswith("moldy_pg_lane_"):
        pytest.fail("pinned-summary concurrency requires a disposable PostgreSQL lane")
    engine = create_async_engine(parsed.set(drivername="postgresql+asyncpg"))
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _seed_conversation(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"pinned-summary-{user_id.hex}@test.local",
                name="Pinned Summary Owner",
                hashed_password="h",
            )
        )
        db.add(
            Model(
                id=model_id,
                provider="openai",
                model_name=f"pinned-summary-{model_id.hex}",
                display_name="Pinned Summary Model",
            )
        )
        db.add(
            Agent(
                id=agent_id,
                user_id=user_id,
                name="Pinned Summary Agent",
                system_prompt="Test summary locking.",
                model_id=model_id,
            )
        )
        db.add(Conversation(id=conversation_id, agent_id=agent_id, title="Lock test"))
        await db.commit()
    return user_id, model_id, agent_id, conversation_id


@pytest.mark.asyncio
async def test_concurrent_first_pins_serialize_without_primary_key_failure() -> None:
    session_factory, engine = _postgres_session_factory()
    user_id, model_id, agent_id, conversation_id = await _seed_conversation(session_factory)
    completed: list[str] = []

    async def pin(message_id: str) -> None:
        source = SummarySource(
            message_id=message_id,
            role="assistant",
            text=f"Answer from {message_id}",
            branch_checkpoint_id="checkpoint-1",
        )
        async with session_factory() as db:
            await pin_summary(db, conversation_id, message_id, (source,))
            await db.commit()
            completed.append(message_id)

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(pin, "assistant-1")
            task_group.start_soon(pin, "assistant-2")

        assert sorted(completed) == ["assistant-1", "assistant-2"]
        async with session_factory() as db:
            persisted = await db.get(ConversationPinnedSummary, conversation_id)
            assert persisted is not None
            assert persisted.source_message_id in completed
            agent = await db.get(Agent, agent_id)
            model = await db.get(Model, model_id)
            user = await db.get(User, user_id)
            assert agent is not None and model is not None and user is not None
            await db.delete(agent)
            await db.flush()
            await db.delete(model)
            await db.delete(user)
            await db.commit()
    finally:
        await engine.dispose()
