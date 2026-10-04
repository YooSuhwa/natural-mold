from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


async def _seed_run(status: str, *, active: bool) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        _, _, agent = await seed_agent(db)
        conversation = Conversation(agent_id=agent.id, title="Deletion lifecycle")
        db.add(conversation)
        await db.flush()
        run = ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            source="chat",
            status=status,
            is_active=active,
        )
        db.add(run)
        await db.commit()
        return agent.id, conversation.id, run.id


@pytest.mark.parametrize("status", ["queued", "running", "canceling"])
async def test_agent_delete_rejects_active_run_and_preserves_graph(
    client: AsyncClient, status: str
) -> None:
    # Given: the agent owns a durable run whose worker still needs its rows.
    agent_id, conversation_id, run_id = await _seed_run(status, active=True)

    # When: the owner deletes the agent through the real HTTP endpoint.
    response = await client.delete(f"/api/agents/{agent_id}")

    # Then: deletion is rejected before cascading into the worker's graph.
    assert response.status_code == 409
    async with TestSession() as db:
        assert await db.get(Agent, agent_id) is not None
        assert await db.get(Conversation, conversation_id) is not None
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        assert run.status == status
        assert run.is_active is True


async def test_agent_delete_accepts_completed_run(client: AsyncClient) -> None:
    # Given: durable finalization has finished and released the active run.
    agent_id, _, _ = await _seed_run("completed", active=False)

    # When: the owner deletes the agent.
    response = await client.delete(f"/api/agents/{agent_id}")

    # Then: the existing successful deletion contract remains available.
    assert response.status_code == 204
