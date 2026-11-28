import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessageChunk
from sqlalchemy import select

from app.agent_runtime import streaming
from app.agent_runtime.event_broker import EventBroker
from app.models.message_event import MessageEventChunk
from app.services import trace_storage
from tests.conftest import TestSession
from tests.test_trace_storage_partial import _seed_conversation


def _four_token_agent() -> MagicMock:
    agent = MagicMock()
    agent.astream.return_value.__aiter__.return_value = [
        (AIMessageChunk(content=token), {}) for token in "abcd"
    ]
    agent.aget_state = AsyncMock(return_value=MagicMock(tasks=[]))
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_limit", [2, 6])
async def test_legacy_retry_marks_only_the_first_retained_journal_frame(
    monkeypatch: pytest.MonkeyPatch, retry_limit: int
) -> None:
    monkeypatch.setattr(streaming, "_FLUSH_BATCH_SIZE", 1)
    monkeypatch.setattr(streaming, "_MAX_RETRY_BUFFER_EVENTS", retry_limit)
    broker = EventBroker("legacy-retry")
    conversation_id = await _seed_conversation()
    trace: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    attempted = asyncio.Event()
    recovered = False

    async def fail_partial_flush(chunk: list[dict[str, Any]]) -> None:
        if not recovered:
            attempted.set()
            raise RuntimeError("partial flush unavailable")
        async with TestSession() as db:
            await trace_storage.append_events(
                db,
                conversation_id=conversation_id,
                assistant_msg_id=broker.run_id,
                events_chunk=chunk,
            )
            await db.commit()
        persisted.extend(chunk)

    wire: list[str] = []
    async for frame in streaming.stream_agent_response(
        _four_token_agent(),
        [],
        {},
        run_id=broker.run_id,
        broker=broker,
        trace_sink=trace,
        persist_callback=fail_partial_flush,
    ):
        wire.append(frame)
        await asyncio.wait_for(attempted.wait(), timeout=2)
        attempted.clear()
        if trace[-1]["event"] == "message_end":
            recovered = True

    assert [event["id"] for event in trace] == [f"legacy-retry-{i}" for i in range(1, 7)]
    assert all(event.keys() == {"id", "event", "data"} for event in trace)
    assert [event async for event in broker.subscribe()] == trace
    assert wire == [
        streaming.format_sse(event["event"], event["data"], event_id=event["id"]) for event in trace
    ]
    expected = trace[-retry_limit:]
    if retry_limit < len(trace):
        expected[0] = {**expected[0], "_moldy_journal_gap_before": True}
    assert persisted == expected
    async with TestSession() as db:
        stored = (
            await db.scalars(
                select(MessageEventChunk).where(MessageEventChunk.assistant_msg_id == broker.run_id)
            )
        ).one()
        assert (stored.seq_start, stored.seq_end) == ((2, 3) if retry_limit == 2 else (1, 6))
        assert stored.events == trace[-retry_limit:]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix_lost", [False, True])
async def test_delayed_legacy_flush_preserves_persisted_order(
    monkeypatch: pytest.MonkeyPatch, prefix_lost: bool
) -> None:
    monkeypatch.setattr(streaming, "_FLUSH_BATCH_SIZE", 1)
    monkeypatch.setattr(streaming, "_MAX_INFLIGHT_FLUSHES", 2)
    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    attempts: list[list[str]] = []
    committed: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    async def delay_first_write(chunk: list[dict[str, Any]]) -> None:
        ids = [event["id"] for event in chunk]
        attempts.append(ids)
        if len(attempts) == 1:
            first_write_started.set()
            await asyncio.wait_for(release_first_write.wait(), timeout=5)
            raise RuntimeError("first write failed")
        if prefix_lost and ids[0] == "ordered-1":
            raise RuntimeError("prefix remains unavailable")
        committed.extend(chunk)

    writes_started_while_blocked = 0
    async for frame in streaming.stream_agent_response(
        _four_token_agent(),
        [],
        {},
        run_id="ordered",
        trace_sink=trace,
        persist_callback=delay_first_write,
    ):
        await asyncio.wait_for(first_write_started.wait(), timeout=2)
        await asyncio.sleep(0)
        if frame.startswith("event: message_end\n"):
            writes_started_while_blocked = len(attempts)
            release_first_write.set()

    assert writes_started_while_blocked == 1
    assert [event["id"] for event in trace] == [f"ordered-{i}" for i in range(1, 7)]
    assert all(event.keys() == {"id", "event", "data"} for event in trace)
    assert attempts[:2] == [["ordered-1"], ["ordered-1", "ordered-2"]]
    if prefix_lost:
        assert committed == [{**trace[2], "_moldy_journal_gap_before": True}, *trace[3:]]
    else:
        assert committed == trace
