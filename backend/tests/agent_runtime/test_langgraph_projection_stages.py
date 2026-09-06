from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.langgraph_event_projection import project_stable_event
from app.agent_runtime.langgraph_stream_ingestion import iter_ingested_protocol_events
from app.agent_runtime.protocol_events import stored_custom_protocol_event, stored_protocol_event
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload


@pytest.mark.asyncio
async def test_ingestion_preserves_arrival_order_and_bounds_malformed_raw_values() -> None:
    """Raw ingestion keeps upstream order without reflecting an opaque malformed value."""

    # Given: out-of-order upstream sequences followed by a non-mapping value.
    raw_events: list[Any] = [
        {
            "type": "event",
            "method": "messages",
            "params": {"namespace": [], "data": {"chunk": "first"}},
            "seq": 9,
            "event_id": "arrival-first",
        },
        {
            "type": "event",
            "method": "messages",
            "params": {"namespace": [], "data": {"chunk": "second"}},
            "seq": 2,
            "event_id": "arrival-second",
        },
        "opaque-upstream-secret",
    ]
    agent = ProtocolAgent(raw_events)

    # When: the production ingestion stage normalizes the stream.
    ingested = [
        item
        async for item in iter_ingested_protocol_events(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-ingestion"}},
            run_id="run-ingestion",
            thread_id="thread-ingestion",
        )
    ]

    # Then: arrival order is retained and malformed input becomes a bounded envelope.
    assert [item.event["upstream_event_id"] for item in ingested[:2]] == [
        "arrival-first",
        "arrival-second",
    ]
    assert ingested[2].event["method"] == "custom"
    assert ingested[2].event["data"] == {"name": "malformed", "payload": None}
    assert "opaque-upstream-secret" not in repr(ingested[2].event)


def test_stable_projection_separates_live_and_persisted_redaction_views() -> None:
    """The stable projection emits one live view and a stricter persisted memory view."""

    # Given: a memory event containing live-only content and a sensitive key.
    event = stored_custom_protocol_event(
        run_id="run-projection",
        thread_id="thread-projection",
        seq=1,
        name="memory_recalled",
        payload={"content": "remember me", "api_key": "must-not-leak"},
    )

    # When: the stable projection builds delivery and persistence views.
    projected = project_stable_event(event, next_seq=1)

    # Then: both views hide secrets while persistence additionally masks memory content.
    live_payload = projected.wire_event["data"]["payload"]
    persisted_payload = projected.persistable_event["data"]["payload"]
    assert live_payload == {"content": "remember me", "api_key": "<redacted>"}
    assert persisted_payload == {"content": "<redacted>", "api_key": "<redacted>"}


@pytest.mark.asyncio
async def test_delivery_resequences_out_of_order_events_without_sorting_or_deduping() -> None:
    """Stable delivery preserves arrival semantics while assigning unique local sequences."""

    # Given: duplicate IDs and decreasing upstream sequences in arrival order.
    first = stored_protocol_event(
        run_id="run-delivery",
        thread_id="thread-delivery",
        seq=5,
        method="messages",
        data={"chunk": "first"},
        event_id="duplicate-upstream-id",
    )
    second = stored_protocol_event(
        run_id="run-delivery",
        thread_id="thread-delivery",
        seq=1,
        method="messages",
        data={"chunk": "second"},
        event_id="duplicate-upstream-id",
    )
    delivery = ProtocolEventDelivery(run_id="run-delivery")

    # When: both records cross the stable delivery boundary.
    chunks = [await delivery.emit(first), await delivery.emit(second)]
    await delivery.close()

    # Then: neither record is removed or sorted, but local sequences are monotonic.
    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["params"]["data"]["chunk"] for payload in payloads] == [
        "first",
        "second",
    ]
    assert [payload["seq"] for payload in payloads] == [5, 6]
    assert [payload["event_id"] for payload in payloads] == [
        "duplicate-upstream-id",
        "duplicate-upstream-id",
    ]
