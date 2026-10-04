from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.schemas.memory import AgentMemorySettingsUpdate
from app.services import memory_service
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


@pytest.mark.parametrize("update", [False, True])
async def test_settings_duplicate_recovery_returns_missing_when_owner_was_deleted(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, update: bool
) -> None:
    # Given: deletion wins after a duplicate INSERT transaction is rolled back.
    _, _, agent = await seed_agent(db)
    await db.commit()
    agent_id = agent.id

    async def deletion_wins_commit() -> None:
        async with TestSession() as other:
            await other.execute(delete(Agent).where(Agent.id == agent_id))
            await other.commit()
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(db, "commit", deletion_wins_commit)

    # When: GET or PATCH tries to recover the competing settings insert.
    if update:
        result = await memory_service.update_agent_settings(
            db, agent_id, TEST_USER_ID, AgentMemorySettingsUpdate(memory_policy_override="off")
        )
    else:
        result = await memory_service.get_agent_settings(db, agent_id, TEST_USER_ID)

    # Then: both router paths can return ownership-safe 404 instead of 500.
    assert result is None
