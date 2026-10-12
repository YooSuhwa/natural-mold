from __future__ import annotations

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.models.agent import Agent
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryManifest
from app.models.conversation_run import ConversationRun
from app.services import agent_purge_service
from tests.agent_purge_service_cases import grant_test_org_admin, seed_focused_purge_history
from tests.conftest import TestSession


async def test_purge_missing_checkpoint_returns_conflict_without_deletion(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    monkeypatch.setattr(agent_purge_service, "get_checkpointer", lambda: InMemorySaver())

    response = await client.post(
        f"/api/agents/{history.agent_id}/purge",
        json={"confirmation_name": "History purge"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
    async with TestSession() as db:
        agent = await db.get(Agent, history.agent_id)
        conversation = await db.get(Conversation, history.conversation_id)
        run = await db.get(ConversationRun, history.run_id)
        receipt = await db.scalar(
            select(AgentPurgeReceipt).where(AgentPurgeReceipt.agent_id_snapshot == history.agent_id)
        )
        assert agent is not None and agent.archived_at is not None
        assert conversation is not None
        assert (conversation.agent_id, conversation.agent_lifecycle_state) == (
            history.agent_id,
            "archived",
        )
        assert run is not None and run.agent_id == history.agent_id
        assert receipt is not None and receipt.state == "preparing"
        assert await db.scalar(select(func.count()).select_from(ConversationHistoryManifest)) == 0
        assert await db.scalar(select(func.count()).select_from(AgentPurgeCleanupItem)) == 0
    raw_checkpoint = await history.saver.aget_tuple(
        {"configurable": {"thread_id": str(history.conversation_id), "checkpoint_ns": ""}}
    )
    assert raw_checkpoint is not None
