import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.services.conversation_run_fence import run_fence_scope
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from tests.agent_runtime.test_chat_recovery_delivery import fenced

pytestmark = pytest.mark.asyncio
INTERVAL = 0.025


def frame(seq: int) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id="journal", thread_id="thread", seq=seq, method="messages", data={"chunk": seq}
    )


async def test_idle_frame_flushes_before_close_only_when_enabled() -> None:
    flushed = asyncio.Event()
    writes: list[list[int]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        writes.append([event["seq"] for event in batch])
        flushed.set()

    ordinary_write = AsyncMock()
    ordinary = ProtocolEventDelivery(run_id="journal", persist_callback=ordinary_write)
    timed = ProtocolEventDelivery(
        run_id="journal", persist_callback=persist, journal_interval=INTERVAL
    )
    await ordinary.emit(frame(0))
    await timed.emit(frame(0))
    await asyncio.wait_for(flushed.wait(), timeout=3)
    await asyncio.sleep(2 * INTERVAL)
    ordinary_write.assert_not_awaited()
    await ordinary.close()
    await timed.close()
    ordinary_write.assert_awaited_once()
    assert writes == [[0]]


async def test_dense_batch_flushes_at_threshold_and_close_drains_the_tail() -> None:
    first_flush = asyncio.Event()
    writes: list[list[int]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        writes.append([event["seq"] for event in batch])
        first_flush.set()

    # This deadline cannot account for the first write within the test.
    writer = ProtocolEventDelivery(run_id="journal", persist_callback=persist, journal_interval=60)
    for seq in range(32):
        await writer.emit(frame(seq))
    await asyncio.wait_for(first_flush.wait(), timeout=3)
    assert writes == [list(range(32))]
    await writer.emit(frame(32))
    await writer.close()
    assert writes == [list(range(32)), [32]]


async def test_synchronous_burst_after_an_idle_flush_stays_in_threshold_sized_batches() -> None:
    first_flush = asyncio.Event()
    writes: list[list[int]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        writes.append([event["seq"] for event in batch])
        first_flush.set()

    writer = ProtocolEventDelivery(
        run_id="journal", persist_callback=persist, journal_interval=INTERVAL
    )
    await writer.emit(frame(0))
    await asyncio.wait_for(first_flush.wait(), timeout=3)
    for seq in range(1, 100):
        await writer.emit(frame(seq))
    await writer.close()
    await asyncio.sleep(2 * INTERVAL)
    assert [len(batch) for batch in writes] == [1, 32, 32, 32, 3]
    assert [seq for batch in writes for seq in batch] == list(range(100))


async def test_slow_writer_applies_backpressure_without_reordering_broker_events() -> None:
    started = asyncio.Event()
    resume = asyncio.Event()
    pending = asyncio.Event()
    writes: list[list[int]] = []
    broker = EventBroker("journal")
    active = 0
    max_active = 0

    async def persist(batch: list[dict[str, Any]]) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
        writes.append([event["seq"] for event in batch])
        started.set()
        try:
            await asyncio.wait_for(resume.wait(), timeout=3)
            await asyncio.sleep(0)
        finally:
            active -= 1

    writer = ProtocolEventDelivery(
        run_id="journal", broker=broker, persist_callback=persist, journal_interval=INTERVAL
    )
    await writer.emit(frame(0))
    await asyncio.wait_for(started.wait(), timeout=3)

    async def produce() -> None:
        for seq in range(1, 66):
            if seq == 31:
                pending.set()
            await writer.emit(frame(seq))

    producer = asyncio.create_task(produce())
    try:
        await asyncio.wait_for(pending.wait(), timeout=3)
        await asyncio.sleep(0)
        assert not producer.done()
        assert writes == [[0]]
    finally:
        resume.set()
        await asyncio.wait_for(producer, timeout=3)
        await writer.close()
    assert [seq for batch in writes for seq in batch] == list(range(66))
    assert max_active == 1
    assert [event["id"] async for event in broker.subscribe()] == [
        frame(i)["id"] for i in range(66)
    ]


@pytest.mark.parametrize("cancel_close", [False, True], ids=["normal", "canceled"])
async def test_close_joins_a_failed_write_and_makes_one_final_attempt(cancel_close: bool) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    writes: list[list[int]] = []
    broker = EventBroker("journal")

    async def persist(batch: list[dict[str, Any]]) -> None:
        writes.append([event["seq"] for event in batch])
        started.set()
        await asyncio.wait_for(release.wait(), timeout=3)
        if len(writes) == 1 or not cancel_close:
            raise RuntimeError("journal unavailable")

    writer = ProtocolEventDelivery(
        run_id="journal", broker=broker, persist_callback=persist, journal_interval=INTERVAL
    )
    await writer.emit(frame(0))
    await asyncio.wait_for(started.wait(), timeout=3)
    await writer.emit(frame(1))
    closing = asyncio.create_task(writer.close())
    await asyncio.sleep(0)
    try:
        if cancel_close:
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done()
        assert not broker.is_closed
    finally:
        release.set()
    if cancel_close:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, timeout=3)
    else:
        await asyncio.wait_for(closing, timeout=3)
    assert broker.is_closed
    await asyncio.sleep(2 * INTERVAL)
    assert writes == [[0], [0, 1]]


async def test_transient_idle_flush_failure_retries_without_another_emit() -> None:
    recovered = asyncio.Event()
    times: list[float] = []
    writes: list[list[int]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        times.append(asyncio.get_running_loop().time())
        writes.append([event["seq"] for event in batch])
        if len(writes) == 1:
            raise RuntimeError("temporary journal outage")
        recovered.set()

    writer = ProtocolEventDelivery(
        run_id="journal", persist_callback=persist, journal_interval=INTERVAL
    )
    await writer.emit(frame(0))
    await asyncio.wait_for(recovered.wait(), timeout=3)
    await asyncio.sleep(2 * INTERVAL)
    await writer.close()
    assert writes == [[0], [0]]
    assert times[1] - times[0] >= INTERVAL / 2


async def test_fenced_delivery_commits_immediately_and_keeps_root_terminal_with_owner() -> None:
    broker = EventBroker("journal")
    writes: list[list[int]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        assert broker.last_event_id is None
        writes.append([event["seq"] for event in batch])

    gate = OwnerTerminalDelivery(
        run_id="journal", broker=broker, persist_callback=persist, trace_sink=[]
    )
    terminal = stored_protocol_event(
        run_id="journal", thread_id="thread", seq=1, method="lifecycle", data={"event": "completed"}
    )
    with run_fence_scope(fenced()):
        writer = ProtocolEventDelivery(
            run_id="journal",
            broker=cast(EventBroker, gate),
            persist_callback=gate.persist,
            journal_interval=INTERVAL,
        )
        event = frame(0)
        await writer.emit(event)
        assert writes == [[0]] and broker.last_event_id == event["id"]
        await writer.emit(terminal)
        await writer.close()
    assert writes == [[0]] and broker.last_event_id == event["id"]
    live, stored = gate.terminal_events_for_status("completed")
    assert live["id"] == stored["id"] == terminal["id"]
    gate.publish_terminal(live, stored)
    assert broker.last_event_id == terminal["id"]
