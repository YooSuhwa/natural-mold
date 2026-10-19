from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryCheckpoint
from app.models.conversation_run import ConversationRun
from app.services.conversation_history_snapshot import (
    HistorySnapshotIncompleteError,
    materialize_conversation_history,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.conversation_history_snapshot_fixture import (
    AGENT_ID,
    authorize_snapshot_agent,
    materialization_request,
    snapshot_case,
)


@pytest.mark.asyncio
async def test_archived_legacy_history_without_participants_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    async with TestSession() as db:
        stored = await db.get(Conversation, case.conversation.id)
        assert stored is not None
        stored.agent_lifecycle_state = "archived"
        stored.agent_archived_at_snapshot = datetime.now(UTC)
        await db.flush()
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []


@pytest.mark.asyncio
async def test_archived_history_with_partial_run_closure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    async with TestSession() as db:
        stored = await db.get(Conversation, case.conversation.id)
        assert stored is not None
        stored.agent_lifecycle_state = "archived"
        stored.agent_archived_at_snapshot = datetime.now(UTC)
        complete_run = ConversationRun(
            conversation_id=case.conversation.id,
            agent_id=AGENT_ID,
            user_id=TEST_USER_ID,
            source="chat",
            status="completed",
            is_active=False,
        )
        missing_run = ConversationRun(
            conversation_id=case.conversation.id,
            agent_id=AGENT_ID,
            user_id=TEST_USER_ID,
            source="chat",
            status="completed",
            is_active=False,
        )
        db.add_all([complete_run, missing_run])
        await db.flush()
        db.add(
            ConversationRunAgent(
                run_id=complete_run.id,
                agent_id=None,
                agent_id_snapshot=AGENT_ID,
                role="root",
                path=[],
            )
        )
        await db.flush()
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []
