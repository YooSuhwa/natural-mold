from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime import langgraph_event_delivery as delivery
from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.journal_continuity import JOURNAL_GAP_BEFORE, trim_journal_retry_buffer
from app.agent_runtime.protocol_events import stored_protocol_event
from app.database import SessionTenant
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services.conversation_stream_persistence import build_persist_callback
from app.services.trace_storage import append_events, load_events, load_events_many
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_trace_storage_partial import _seed_conversation


@pytest.mark.asyncio
async def test_protocol_retry_eviction_reserves_one_durable_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery, "_FLUSH_BATCH_SIZE", 1)
    monkeypatch.setattr(delivery, "_MAX_RETRY_BUFFER_EVENTS", 2)
    conv_id = await _seed_conversation()
    physical_run = uuid4()
    run_id = str(physical_run)
    # Pre-chunk legacy history can also contain frames without SSE IDs.
    async with TestSession() as db:
        db.add(MessageEvent(conversation_id=conv_id, assistant_msg_id=run_id, events=[{}, {}]))
        db.add(
            ConversationRun(
                id=physical_run,
                conversation_id=conv_id,
                user_id=TEST_USER_ID,
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                source="chat",
                status="running",
                is_active=True,
            )
        )
        await db.commit()

    append = build_persist_callback(conv_id, run_id, tenant=SessionTenant(TEST_TENANT_ID))
    final_batch: list[dict[str, Any]] = []
    release_write = asyncio.Event()

    async def write(batch: list[dict[str, Any]]) -> None:
        if not release_write.is_set():
            await release_write.wait()
            raise RuntimeError("write unavailable")
        final_batch.extend(batch)
        await append(batch)

    trace: list[dict[str, object]] = []
    broker = EventBroker(run_id)
    writer = delivery.ProtocolEventDelivery(
        run_id=run_id, broker=broker, trace_sink=trace, persist_callback=write
    )
    wire: list[str] = []
    for seq in range(4):
        event = stored_protocol_event(
            run_id=run_id, thread_id=str(conv_id), seq=seq, method="messages", data={"chunk": seq}
        )
        wire.append(await writer.emit(event))
        if seq == 0:
            # Start the pending first write before accumulating the rest.
            await asyncio.sleep(0)
    release_write.set()
    await writer.close()

    assert len(final_batch) == 2
    assert final_batch[0][JOURNAL_GAP_BEFORE] is True
    assert JOURNAL_GAP_BEFORE not in final_batch[1]
    assert [evt["id"] for evt in final_batch] == [evt["id"] for evt in trace[-2:]]
    assert all(JOURNAL_GAP_BEFORE not in frame for frame in wire)
    assert all(JOURNAL_GAP_BEFORE not in evt for evt in trace)
    published = [event async for event in broker.subscribe()]
    assert [evt["id"] for evt in published] == [evt["id"] for evt in trace]
    assert JOURNAL_GAP_BEFORE not in str(published)

    # A retry carrying the marker may partially overlap the committed batch.
    await append(final_batch)
    await append([*final_batch, {"id": "next", "data": {JOURNAL_GAP_BEFORE: "user value"}}])
    await append(final_batch)
    async with TestSession() as db:
        rows = (
            await db.scalars(select(MessageEventChunk).order_by(MessageEventChunk.seq_start))
        ).all()
        assert [(row.seq_start, row.seq_end) for row in rows] == [(4, 5), (6, 6)]
        assert rows[0].events == trace[-2:]
        record = (
            await db.scalars(select(MessageEvent).where(MessageEvent.assistant_msg_id == run_id))
        ).one()
        assert record.last_event_id == "next"
        run = await db.get(ConversationRun, physical_run)
        assert run is not None and run.last_event_id == record.last_event_id
        history = await load_events(db, record)
        assert history == [
            {},
            {},
            *trace[-2:],
            {"id": "next", "data": {JOURNAL_GAP_BEFORE: "user value"}},
        ]


def test_repeated_eviction_moves_the_marker_to_a_copy_of_the_new_survivor() -> None:
    original = [{"id": str(index)} for index in range(5)]
    retry: list[dict[str, Any]] = list(original[:3])
    assert trim_journal_retry_buffer(retry, 2) == 1
    assert retry == [{"id": "1", JOURNAL_GAP_BEFORE: True}, {"id": "2"}]
    retry.extend(original[3:])
    assert trim_journal_retry_buffer(retry, 2) == 2
    assert retry == [{"id": "3", JOURNAL_GAP_BEFORE: True}, {"id": "4"}]
    assert all(JOURNAL_GAP_BEFORE not in item for item in original)


@pytest.mark.asyncio
async def test_trace_reads_and_appends_ignore_chunks_with_mismatched_turn_references() -> None:
    conv_id, other_conv = await _seed_conversation(), await _seed_conversation()
    run_id = "scoped-run"
    async with TestSession() as db:
        record = MessageEvent(conversation_id=conv_id, assistant_msg_id=run_id, events=[])
        db.add(record)
        await db.flush()
        for idx, (conversation, assistant_id) in enumerate(
            [(other_conv, run_id), (conv_id, "other-run")], 1
        ):
            db.add(
                MessageEventChunk(
                    message_event_id=record.id,
                    conversation_id=conversation,
                    assistant_msg_id=assistant_id,
                    seq_start=200 + idx,
                    seq_end=200 + idx,
                    first_event_id=f"mismatch-{idx}",
                    last_event_id=f"mismatch-{idx}",
                    event_ids=[f"mismatch-{idx}"],
                    events=[{"id": f"mismatch-{idx}", "data": "other turn"}],
                )
            )
        await db.commit()

        expected = [{"id": "current", "data": "this turn"}]
        await append_events(
            db, conversation_id=conv_id, assistant_msg_id=run_id, events_chunk=expected
        )
        await db.commit()
        valid = await db.scalar(
            select(MessageEventChunk).where(MessageEventChunk.first_event_id == "current")
        )
        assert valid is not None and (valid.seq_start, valid.seq_end) == (1, 1)
        assert await load_events(db, record) == expected
        assert await load_events_many(db, [record]) == {record.id: expected}
