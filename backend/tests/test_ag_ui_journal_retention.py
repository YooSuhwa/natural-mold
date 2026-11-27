"""A real sanitized history capture retains committed journal holes after agent purge."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.models.conversation_history import ConversationHistoryEvent
from app.models.message_event import MessageEventChunk
from app.services.ag_ui_journal import read_journal_page
from app.services.agent_purge_history_validation import verify_history_manifest
from app.services.conversation_history_snapshot import materialize_conversation_history
from app.services.trace_storage import append_events
from tests.conftest import TestSession
from tests.conversation_history_snapshot_fixture import (
    authorize_snapshot_agent,
    collect_no_secrets,
    materialization_request,
    snapshot_case,
)
from tests.test_ag_ui_journal import _conversation, _raw


async def test_agent_purge_retains_gap_in_authenticated_source_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    cid, rid = case.conversation.id, uuid4()
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.collect_strict_history_secrets",
        collect_no_secrets,
    )
    async with TestSession() as db:
        for n in (1, 3):
            await append_events(
                db, conversation_id=cid, assistant_msg_id=str(rid), events_chunk=[_raw(n, rid, cid)]
            )
            await db.flush()
        # A terminal flush survived but the batch immediately before it was lost.
        await db.execute(
            update(MessageEventChunk)
            .where(MessageEventChunk.assistant_msg_id == str(rid), MessageEventChunk.seq_start == 2)
            .values(seq_start=3, seq_end=3)
        )
        manifest = await materialize_conversation_history(materialization_request(db, case))
        await db.commit()
        assert manifest.event_count == 2 and await verify_history_manifest(db, manifest)
        records = list(
            await db.scalars(
                select(ConversationHistoryEvent)
                .where(ConversationHistoryEvent.manifest_id == manifest.id)
                .order_by(ConversationHistoryEvent.sequence_start)
            )
        )
        assert [(row.sequence_start, row.sequence_end) for row in records] == [(0, 0), (2, 2)]
        conversation = await _conversation(db, cid)
        conversation.agent_lifecycle_state = "purged"
        conversation.agent_id = None
        conversation.agent_purged_at = datetime.now(UTC)
        await db.commit()
        first = await read_journal_page(db, conversation=conversation, physical_run_id=rid, limit=1)
        assert (
            first.high_water == 3 and first.has_more and [e.ordinal for e in first.entries] == [1]
        )
        missing = await read_journal_page(
            db, conversation=conversation, physical_run_id=rid, after=1
        )
        assert missing.gap and not missing.entries
        # Tampering with a retained range while preserving the event checksum
        # cannot make the archive pass the transaction-B verification.
        records[1].sequence_start = records[1].sequence_end = 1
        await db.flush()
        assert not await verify_history_manifest(db, manifest)
        tampered = await read_journal_page(
            db, conversation=conversation, physical_run_id=rid, after=1
        )
        assert tampered.gap and not tampered.entries
