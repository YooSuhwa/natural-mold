from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping
from typing import Any, cast

import pytest

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.streaming import stream_agent_response
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery


def _terminal_status(event: Mapping[str, object]) -> str | None:
    terminal_statuses = {"completed", "canceled", "failed", "interrupted", "stale"}
    if event.get("event") == "message_end":
        data = event.get("data")
        status = data.get("status") if isinstance(data, Mapping) else None
        return status if status in terminal_statuses else None
    return None


class _EmptyLegacyAgent:
    async def astream(self, _input: object, **_kwargs: object) -> AsyncGenerator[object, None]:
        if False:
            yield object()

    async def aget_state(self, _config: object) -> object:
        return type("State", (), {"tasks": []})()


@pytest.mark.asyncio
async def test_legacy_stream_close_is_deferred_to_owner_terminal_gate() -> None:
    run_id = str(uuid.uuid4())
    broker = EventBroker(run_id)
    persisted: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.extend(events)

    gate = OwnerTerminalDelivery(
        run_id=run_id,
        broker=broker,
        persist_callback=persist,
        trace_sink=trace,
    )
    _ = [
        chunk
        async for chunk in stream_agent_response(
            _EmptyLegacyAgent(),
            [],
            {},
            run_id=run_id,
            broker=cast(EventBroker, gate),
            persist_callback=gate.persist,
            trace_sink=trace,
        )
    ]

    assert broker.is_closed is False
    assert all(_terminal_status(event) is None for event in broker._buffer)
    assert all(_terminal_status(event) is None for event in persisted)
    live_terminal, persisted_terminal = gate.terminal_events_for_status("completed")
    gate.publish_terminal(live_terminal, persisted_terminal)
    assert _terminal_status(list(broker._buffer)[-1]) == "completed"


@pytest.mark.asyncio
async def test_owner_terminal_gate_does_not_hold_nested_lifecycle() -> None:
    run_id = str(uuid.uuid4())
    broker = EventBroker(run_id)
    persisted: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.extend(events)

    gate = OwnerTerminalDelivery(
        run_id=run_id,
        broker=broker,
        persist_callback=persist,
        trace_sink=trace,
    )
    delivery = ProtocolEventDelivery(
        run_id=run_id,
        trace_sink=trace,
        broker=cast(EventBroker, gate),
        persist_callback=gate.persist,
    )
    nested = stored_protocol_event(
        run_id=run_id,
        thread_id="thread",
        seq=0,
        method="lifecycle",
        namespace=["subagent"],
        data={"event": "completed"},
    )

    await delivery.emit(nested)
    await delivery.close()

    assert list(broker._buffer)[0]["id"] == nested["id"]
    assert persisted[0]["namespace"] == ["subagent"]
    assert broker.is_closed is False
