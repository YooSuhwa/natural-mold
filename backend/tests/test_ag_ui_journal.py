"""The public read transport follows committed source ordinals, including pre-chunk histories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryEvent, ConversationHistoryManifest
from app.models.message_event import MessageEvent, MessageEventChunk
from app.schemas.conversation_history import JsonValue
from app.services.ag_ui_journal import read_journal_page
from app.services.conversation_history_projection import canonical_payload_sha256
from app.services.trace_storage import append_events, finalize_turn
from tests.conftest import TestSession
from tests.test_trace_storage_partial import _seed_conversation


def _raw(n: int, run: UUID, conversation: UUID, *, text: str = "answer") -> dict[str, Any]:
    return {
        "id": f"source-{n}",
        "seq": 100 + n * 3,
        "method": "messages",
        "run_id": str(run),
        "thread_id": str(conversation),
        "namespace": [],
        "data": {"message": {"id": f"model-{n}", "type": "ai", "content": text}},
    }


async def _conversation(db: AsyncSession, id_: UUID) -> Conversation:
    conversation = await db.get(Conversation, id_)
    assert conversation
    return conversation


async def test_public_reader_uses_only_committed_scope_and_raw_ordinal() -> None:
    cid = await _seed_conversation()
    other = await _seed_conversation()
    rid = uuid4()
    async with TestSession() as write:
        record = MessageEvent(
            conversation_id=cid,
            assistant_msg_id=str(rid),
            events=[_raw(0, rid, cid)],
            status="streaming",
        )
        write.add(record)
        await write.commit()
        await append_events(
            write,
            conversation_id=cid,
            assistant_msg_id=str(rid),
            events_chunk=[_raw(i, rid, cid) for i in (1, 2)],
        )
        await write.commit()
    async with TestSession() as read:
        conversation = await _conversation(read, cid)
        statements: list[str] = []

        def observe(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            statements.append(statement.lstrip().split()[0].upper())

        engine = read.get_bind()
        event.listen(engine, "before_cursor_execute", observe)
        try:
            first = await read_journal_page(
                read, conversation=conversation, physical_run_id=rid, limit=2
            )
            next_page = await read_journal_page(
                read, conversation=conversation, physical_run_id=rid, after=2
            )
            other_page = await read_journal_page(
                read, conversation=await _conversation(read, other), physical_run_id=rid
            )
            beyond = await read_journal_page(
                read, conversation=conversation, physical_run_id=rid, after=4
            )
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert set(statements) == {"SELECT"}
        assert not first.gap and first.has_more and first.trace_status == "streaming"
        assert [(e.ordinal, e.source_id, e.events[0]["seq"]) for e in first.entries] == [
            (1, "source-0", 100),
            (2, "source-1", 103),
        ]
        assert first.high_water == next_page.high_water == 3
        assert first.generation == next_page.generation
        assert not next_page.gap and not next_page.has_more
        assert [(e.ordinal, e.source_id) for e in next_page.entries] == [(3, "source-2")]
        assert not other_page.entries and other_page.generation is None
        assert beyond.gap

    async with TestSession() as write:
        await append_events(
            write,
            conversation_id=cid,
            assistant_msg_id=str(rid),
            events_chunk=[_raw(3, rid, cid, text="journal-secret-value")],
        )
        await finalize_turn(write, assistant_msg_id=str(rid))
        await write.commit()
    async with TestSession() as read:
        result = await read_journal_page(
            read,
            conversation=await _conversation(read, cid),
            physical_run_id=rid,
            after=3,
            secret_values=("journal-secret-value",),
        )
        assert result.generation == first.generation and result.trace_status == "completed"
        assert result.high_water == 4
        assert [entry.ordinal for entry in result.entries] == [4]
        assert "journal-secret-value" not in str(result.entries)


@pytest.mark.parametrize("corruption", ["gap", "overlap", "length", "ids", "scope"])
async def test_gap_in_committed_ranges_requests_snapshot(corruption: str) -> None:
    cid = await _seed_conversation()
    rid = uuid4()
    async with TestSession() as db:
        for n in (1, 2):
            await append_events(
                db, conversation_id=cid, assistant_msg_id=str(rid), events_chunk=[_raw(n, rid, cid)]
            )
            await db.commit()
        changes: dict[str, Any]
        if corruption == "gap":
            changes = {"seq_start": 3, "seq_end": 3}
        elif corruption == "overlap":
            changes = {"seq_start": 1, "seq_end": 1}
        elif corruption == "length":
            changes = {"seq_end": 4}
        elif corruption == "scope":
            changes = {"events": [_raw(2, rid, uuid4())]}
        else:
            changes = {"event_ids": ["not-source-2"]}
        await db.execute(
            update(MessageEventChunk)
            .where(MessageEventChunk.assistant_msg_id == str(rid), MessageEventChunk.seq_start == 2)
            .values(**changes)
        )
        await db.commit()
        result = await read_journal_page(
            db, conversation=await _conversation(db, cid), physical_run_id=rid
        )
        assert result.gap and not result.entries


async def test_partial_journal_rolls_back_without_publishing_a_cursor() -> None:
    cid = await _seed_conversation()
    rid = uuid4()
    async with TestSession() as db:
        await append_events(
            db, conversation_id=cid, assistant_msg_id=str(rid), events_chunk=[_raw(1, rid, cid)]
        )
        await db.flush()
        await db.rollback()
    async with TestSession() as db:
        before = await read_journal_page(
            db, conversation=await _conversation(db, cid), physical_run_id=rid
        )
        assert not before.entries and before.generation is None
        await append_events(
            db, conversation_id=cid, assistant_msg_id=str(rid), events_chunk=[_raw(1, rid, cid)]
        )
        await db.commit()
        after = await read_journal_page(
            db, conversation=await _conversation(db, cid), physical_run_id=rid
        )
        assert [e.ordinal for e in after.entries] == [1]
        assert after.generation is not None


async def test_journal_only_follows_complete_verified_retained_records() -> None:
    cid = await _seed_conversation()
    rid = uuid4()
    async with TestSession() as db:
        conversation = await _conversation(db, cid)
        agent_id = conversation.agent_id
        assert agent_id
        # Agent purge preserves the conversation and writes a complete history manifest.
        conversation.agent_lifecycle_state = "purged"
        conversation.agent_id = None
        conversation.agent_purged_at = datetime.now(UTC)
        manifest = ConversationHistoryManifest(
            conversation_id=cid,
            source_agent_id_snapshot=agent_id,
            state="building",
            checkpoint_count=1,
            event_count=2,
            canonical_payload_sha256="0" * 64,
        )
        db.add(manifest)
        await db.flush()
        evts: list[JsonValue] = [_raw(1, rid, cid), _raw(2, rid, cid)]
        db.add(
            ConversationHistoryEvent(
                manifest_id=manifest.id,
                conversation_id=cid,
                assistant_message_id=str(rid),
                sequence_start=0,
                sequence_end=1,
                events=evts,
                payload_sha256=canonical_payload_sha256(evts),
            )
        )
        await db.commit()
        incomplete = await read_journal_page(db, conversation=conversation, physical_run_id=rid)
        assert incomplete.gap and not incomplete.entries
        manifest.state = "complete"
        await db.commit()
        first = await read_journal_page(db, conversation=conversation, physical_run_id=rid, limit=1)
        second = await read_journal_page(
            db, conversation=conversation, physical_run_id=rid, after=1
        )
        assert first.high_water == second.high_water == 2
        assert first.has_more and first.generation == str(manifest.id)
        assert not second.has_more and [e.ordinal for e in second.entries] == [2]
        assert second.trace_status == "completed"
        archived = await db.scalar(
            select(ConversationHistoryEvent).where(
                ConversationHistoryEvent.manifest_id == manifest.id
            )
        )
        assert archived
        archived.payload_sha256 = "1" * 64
        await db.commit()
        invalid = await read_journal_page(db, conversation=conversation, physical_run_id=rid)
        assert invalid.gap and not invalid.entries


async def test_legacy_user_and_output_stay_in_root_scope() -> None:
    cid = await _seed_conversation()
    rid = uuid4()
    start = {
        "id": "start",
        "event": "message_start",
        "data": {
            "id": str(rid),
            "input": {
                "messages": [
                    {"role": "system", "content": "private prompt"},
                    {"role": "user", "id": "user-input", "content": "submitted test-key-value"},
                ]
            },
        },
    }
    child = {
        "id": "child",
        "event": "content_delta",
        "data": {"delta": "nested text", "checkpoint_ns": "child:1"},
    }
    async with TestSession() as db:
        await append_events(
            db, conversation_id=cid, assistant_msg_id=str(rid), events_chunk=[start, child]
        )
        await db.commit()
        conv = await _conversation(db, cid)
        page = await read_journal_page(
            db, conversation=conv, physical_run_id=rid, limit=1, secret_values=("test-key-value",)
        )
        assert page.high_water == 2 and page.has_more
        (user,) = page.entries[0].legacy_users
        assert user["id"] == "user-input" and user["role"] == "user"
        assert "submitted" in user["content"]
        assert all(text not in str(page.entries) for text in ("test-key-value", "private prompt"))
        assert not page.entries[0].events
        gap = await read_journal_page(db, conversation=conv, physical_run_id=rid, after=1)
        assert gap.gap and not gap.entries


async def test_legacy_partial_flush_committed_out_of_emission_order() -> None:
    cid = await _seed_conversation()
    rid = uuid4()
    async with TestSession() as db:
        await append_events(
            db,
            conversation_id=cid,
            assistant_msg_id=str(rid),
            events_chunk=[{"id": f"{rid}-2", "event": "content_delta", "data": {"delta": "later"}}],
        )
        await db.commit()
        page = await read_journal_page(
            db, conversation=await _conversation(db, cid), physical_run_id=rid
        )
        assert page.gap and not page.entries
