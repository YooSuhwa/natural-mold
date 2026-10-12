from __future__ import annotations

import uuid
from datetime import UTC as _FIXTURE_UTC
from datetime import datetime as _fixture_datetime

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


async def _seed_run(status: str, *, active: bool) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        _, _, agent = await seed_agent(db)
        conversation = Conversation(
            agent_id=agent.id,
            title="Deletion lifecycle",
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=agent.user_id,
            agent_name_snapshot=agent.name,
            last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
        )
        db.add(conversation)
        await db.flush()
        run = ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            source="chat",
            status=status,
            is_active=active,
            cancel_requested_at=(
                _fixture_datetime.now(_FIXTURE_UTC).replace(tzinfo=None)
                if status == "canceling"
                else None
            ),
            cancel_reason="agent_archived" if status == "canceling" else None,
        )
        db.add(run)
        await db.flush()
        db.add(
            ConversationRunAgent(
                run_id=run.id,
                agent_id=agent.id,
                agent_id_snapshot=agent.id,
                role="root",
                path=[],
            )
        )
        await db.commit()
        return agent.id, conversation.id, run.id


@pytest.mark.parametrize("status", ["queued", "running", "canceling"])
async def test_agent_delete_archives_and_requests_active_run_cancellation(
    client: AsyncClient, status: str
) -> None:
    # Given: the agent owns a durable run whose worker still needs its rows.
    agent_id, conversation_id, run_id = await _seed_run(status, active=True)

    # When: the owner deletes the agent through the real HTTP endpoint.
    response = await client.delete(f"/api/agents/{agent_id}")

    # Then: archive preserves the graph and durably requests owner cancellation.
    assert response.status_code == 204
    async with TestSession() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert agent.archived_at is not None
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_id == agent_id
        assert conversation.agent_lifecycle_state == "archived"
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        assert run.status == "canceling"
        assert run.cancel_requested_at is not None
        assert run.cancellation_acknowledged_at is None

    # When: restore is attempted before the durable owner acknowledgment.
    restore = await client.post(f"/api/agents/{agent_id}/restore")

    # Then: archived state remains unchanged behind the typed conflict.
    assert restore.status_code == 409
    assert restore.json()["error"]["code"] == "AGENT_LIFECYCLE_EXECUTION_PENDING"


async def test_agent_delete_archives_completed_history_without_physical_delete(
    client: AsyncClient,
) -> None:
    # Given: durable finalization has finished and released the active run.
    agent_id, conversation_id, run_id = await _seed_run("completed", active=False)

    # When: the owner deletes the agent.
    response = await client.delete(f"/api/agents/{agent_id}")

    # Then: compatibility DELETE archives while retaining completed history.
    assert response.status_code == 204
    async with TestSession() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None and agent.archived_at is not None
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_lifecycle_state == "archived"
        assert await db.get(ConversationRun, run_id) is not None
