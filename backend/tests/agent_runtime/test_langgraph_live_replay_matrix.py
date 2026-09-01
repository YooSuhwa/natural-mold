from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

import pytest

from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.langgraph_lifecycle_events import lifecycle_protocol_event
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_replay import (
    _stored_events_from_raw,
    protocol_stale_event,
)
from tests.agent_runtime.langgraph_streaming_fixtures import sse_payload

_RUN_ID = "run-live-replay-matrix"
_THREAD_ID = "thread-live-replay-matrix"
_TIMING_KEYS = ("ttft_ms", "generation_ms", "tokens_per_second")


def _captured_scenarios() -> list[tuple[str, StoredProtocolEvent]]:
    """Rebuild runtime_wire/resume and stale-replay cases with production constructors."""

    return [
        (
            "partial",
            stored_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=1,
                method="messages",
                data={"id": "assistant-partial", "content": "partial"},
                event_id="captured-partial",
            ),
        ),
        (
            "hitl",
            stored_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=2,
                method="input.requested",
                data={
                    "interrupt_id": "interrupt-1",
                    "payload": {
                        "action_requests": [{"name": "execute_in_skill", "args": {}}],
                        "review_configs": [
                            {
                                "action_name": "execute_in_skill",
                                "allowed_decisions": ["approve", "reject"],
                            }
                        ],
                    },
                },
                event_id="captured-hitl",
                checkpoint_id="checkpoint-hitl",
                checkpoint_ns="tools:execute",
            ),
        ),
        (
            "interrupted",
            lifecycle_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=3,
                event="interrupted",
            ),
        ),
        (
            "resumed",
            lifecycle_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=4,
                event="running",
            ),
        ),
        (
            "subagent",
            stored_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=5,
                method="subagents",
                namespace=["agent:research"],
                data={"id": "subagent-1", "name": "research", "status": "complete"},
                event_id="captured-subagent",
            ),
        ),
        (
            "artifact",
            stored_custom_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=6,
                name="file_event",
                payload={"op": "created", "id": "artifact-1", "path": "report.md"},
                event_id="captured-artifact",
            ),
        ),
        (
            "usage",
            stored_custom_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=7,
                name="usage",
                payload={
                    "run_id": _RUN_ID,
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 1,
                    "ttft_ms": 12.5,
                    "generation_ms": 25.0,
                    "tokens_per_second": 80.0,
                },
                event_id="captured-usage",
            ),
        ),
        (
            "error",
            stored_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=8,
                method="error",
                data={"message": "public failure"},
                event_id="captured-error",
            ),
        ),
        (
            "stale",
            protocol_stale_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=9,
                last_event_id="captured-error",
            ),
        ),
        (
            "complete",
            lifecycle_protocol_event(
                run_id=_RUN_ID,
                thread_id=_THREAD_ID,
                seq=10,
                event="completed",
            ),
        ),
    ]


def _stable_data(method: str, data: Any) -> Any:
    stable = deepcopy(data)
    if method == "custom" and isinstance(stable, dict) and stable.get("name") == "usage":
        payload = stable.get("payload")
        if isinstance(payload, dict):
            for key in _TIMING_KEYS:
                payload.pop(key, None)
    return stable


def _wire_contract(event: dict[str, Any]) -> tuple[Any, ...]:
    params = event["params"]
    return (
        event["method"],
        params["namespace"],
        _stable_data(event["method"], params["data"]),
        event["seq"],
        event["event_id"],
        params.get("checkpoint_id"),
        params.get("checkpoint_ns"),
    )


def _replay_contract(event: StoredProtocolEvent) -> tuple[Any, ...]:
    return (
        event["method"],
        event["namespace"],
        _stable_data(event["method"], event["data"]),
        event["seq"],
        event["upstream_event_id"] or event["id"],
        event["checkpoint_id"],
        event["checkpoint_ns"],
    )


@pytest.mark.asyncio
async def test_captured_scenarios_keep_stable_live_persisted_replay_contracts() -> None:
    """All reviewed runtime cases retain stable fields, order, and IDs through replay."""

    scenarios = _captured_scenarios()
    persisted_batches: list[list[dict[str, Any]]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted_batches.append(deepcopy(events))

    delivery = ProtocolEventDelivery(run_id=_RUN_ID, persist_callback=persist)
    live = [sse_payload(await delivery.emit(event)) for _, event in scenarios]
    await delivery.close()
    persisted = [event for batch in persisted_batches for event in batch]
    record = MessageEvent(
        conversation_id=uuid.UUID("90000000-0000-0000-0000-000000000011"),
        assistant_msg_id=_RUN_ID,
        events=[],
        status="completed",
    )
    replayed = [
        replay
        for index, raw in enumerate(persisted, start=1)
        for replay in _stored_events_from_raw(raw, record=record, fallback_seq=index)
    ]

    assert [name for name, _ in scenarios] == [
        "partial",
        "hitl",
        "interrupted",
        "resumed",
        "subagent",
        "artifact",
        "usage",
        "error",
        "stale",
        "complete",
    ]
    assert len(live) == len(persisted) == len(replayed) == len(scenarios)
    assert [_wire_contract(event) for event in live] == [
        _replay_contract(event) for event in replayed
    ]
