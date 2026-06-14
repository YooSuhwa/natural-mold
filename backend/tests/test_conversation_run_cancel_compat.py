from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service
from tests.conftest import TEST_USER_ID


async def _seed_running_run(db: AsyncSession) -> tuple[Conversation, uuid.UUID]:
    user = User(id=TEST_USER_ID, email="compat-cancel@test.local", name="Compat Cancel")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Compat Cancel Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="Compat cancel")
    db.add(conversation)
    await db.flush()
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=user.id,
        source="chat",
        input_preview="cancel through sdk path",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="lost")
    await db.commit()
    return conversation, run.id


@pytest.mark.asyncio
async def test_langgraph_sdk_cancel_path_uses_conversation_run_cancel(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation, run_id = await _seed_running_run(db)

    response = await client.post(f"/threads/{conversation.id}/runs/{run_id}/cancel")

    assert response.status_code == 202
    assert response.content == b""
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation.id,
        run_id=run_id,
        user_id=TEST_USER_ID,
    )
    assert run is not None
    assert run.status == "canceled"
