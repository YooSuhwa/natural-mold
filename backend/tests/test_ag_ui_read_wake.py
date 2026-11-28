"""A cross-worker wake-up is a committed, scoped hint with no message payload."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import conversation_run_service, trace_storage
from app.services.ag_ui_read_wake import committed_run_changed
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


@pytest.mark.asyncio
async def test_committed_hint_for_running_chat_and_new_journal_row(db: AsyncSession) -> None:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, conversation=conversation, agent=agent)
    context = unit_context(agent.user_id)
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="writer")
    await db.commit()
    assert not await committed_run_changed(db, context, run, None, 0, None)

    record = await trace_storage.append_events(
        db,
        conversation_id=conversation.id,
        assistant_msg_id=str(run.id),
        events_chunk=[{"event": "token", "id": "sample:1", "data": {"text": "a"}}],
    )
    assert record
    await db.commit()
    assert await committed_run_changed(db, context, run, None, 0, None)
    assert await committed_run_changed(db, context, run, str(record.id), 0, "streaming")
    assert not await committed_run_changed(db, context, run, str(record.id), 1, "streaming")

    await trace_storage.finalize_turn(db, assistant_msg_id=str(run.id), status="completed")
    await db.commit()
    assert await committed_run_changed(db, context, run, str(record.id), 1, "streaming")
