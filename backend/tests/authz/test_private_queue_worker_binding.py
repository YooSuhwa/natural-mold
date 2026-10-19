"""Corrupt queue bindings cannot execute or mutate unrelated conversation runs."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_worker as worker
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_private_queue_worker_read_order import private_selects
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("launch", [False, True])
async def test_foreign_run_binding_fails_input_without_mutating_foreign_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, launch: bool
) -> None:
    # Given a claimed input linked to a run belonging to another conversation.
    conversation_id = await seed_conversation_with_agent()
    foreign_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    foreign = await db.get(Conversation, foreign_id)
    assert conversation is not None and foreign is not None
    run = ConversationRun(
        conversation_id=foreign.id,
        agent_id=foreign.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.flush()
    queued = ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        run_id=run.id,
        source="chat",
        status="claimed",
        client_request_id="corrupt-binding",
        position=1,
        revision=1,
        input_payload={"messages": []},
        attachment_ids=[],
    )
    db.add(queued)
    await db.commit()
    monkeypatch.setattr(worker, "async_session", TestSession)

    # When launch or failure recovery handles the malformed claimed input.
    with queue_resource_sql(db) as statements:
        if launch:
            assert await worker.launch_claimed_input(queued.id) is None
        else:
            await worker._fail_claimed_input(queued.id, error_code="queue_binding_invalid")

    # Then only that input is failed; the unrelated run remains queued.
    await db.refresh(queued)
    await db.refresh(run)
    assert queued.status == "failed"
    assert run.status == "queued" and run.is_active is True
    assert private_selects(statements) == []
    assert queued.revision == 2
