"""Golden contract coverage for the LangGraph raw-to-wire stream boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.agent_runtime.langgraph_protocol_adapter import adapt_v3_protocol_event
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from tests.agent_runtime.langgraph_streaming_fixtures import (
    FakeArtifactRecorder,
    ProtocolAgent,
    sse_payload,
)
from tests.agent_runtime.runtime_contract_helpers import (
    assert_contract_matches,
    contract_diff_paths,
    load_contract_fixture,
)

_RUN_ID = "run-wire-contract-1"
_THREAD_ID = "thread-wire-contract-1"
_TIMING_KEYS = ("ttft_ms", "generation_ms", "tokens_per_second")


def _raw_events() -> list[dict[str, Any]]:
    """Return fixed v3 records spanning the public runtime stream boundary."""

    return [
        {
            "type": "event",
            "method": "messages",
            "params": {
                "namespace": [],
                "data": {
                    "id": "assistant-wire-1",
                    "type": "AIMessageChunk",
                    "content": "보고서를 만들겠습니다.",
                    "tool_calls": [
                        {
                            "id": "call-report-1",
                            "name": "create_report",
                            "args": {"title": "주간 보고서"},
                        }
                    ],
                    "usage_metadata": {
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "input_token_details": {"cache_creation": 2, "cache_read": 3},
                    },
                },
            },
            "seq": 1,
            "event_id": "raw-message-1",
        },
        {
            "type": "event",
            "method": "values",
            "params": {
                "namespace": ["tools:call-report-1"],
                "checkpoint": {
                    "checkpoint_id": "checkpoint-wire-1",
                    "checkpoint_ns": "tools:call-report-1",
                },
                "data": {
                    "messages": [
                        {
                            "id": "assistant-wire-1",
                            "type": "ai",
                            "content": "보고서를 만들겠습니다.",
                            "tool_calls": [
                                {
                                    "id": "call-report-1",
                                    "name": "create_report",
                                    "args": {"title": "주간 보고서"},
                                }
                            ],
                        },
                        {
                            "id": "tool-wire-1",
                            "type": "tool",
                            "name": "create_report",
                            "tool_call_id": "call-report-1",
                            "content": "report.md created",
                        },
                    ],
                    "todos": [{"id": "todo-wire-1", "content": "작성", "status": "done"}],
                    "__interrupt__": [
                        {
                            "id": "interrupt-wire-1",
                            "value": {
                                "action_requests": [
                                    {"name": "create_report", "args": {"title": "주간 보고서"}}
                                ],
                                "review_configs": [
                                    {
                                        "action_name": "create_report",
                                        "allowed_decisions": ["approve", "reject"],
                                    }
                                ],
                            },
                        }
                    ],
                },
            },
            "seq": 2,
            "event_id": "raw-values-1",
        },
        {
            "type": "event",
            "method": "vendor.progress",
            "params": {"namespace": ["tools:call-report-1"], "data": {"stage": "done"}},
            "seq": 3,
            "event_id": "raw-vendor-1",
        },
        {
            "type": "event",
            "method": "custom:malformed_payload",
            "params": "not-a-mapping",
            "seq": 4,
            "event_id": "raw-malformed-1",
        },
    ]


def _stable_event(event: dict[str, Any], *, is_wire: bool) -> dict[str, Any]:
    """Remove monotonic timing values while retaining every stable protocol field."""

    stable = deepcopy(event)
    data = stable["params"]["data"] if is_wire else stable["data"]
    if stable["method"] == "custom" and data.get("name") == "usage":
        for key in _TIMING_KEYS:
            data["payload"].pop(key, None)
    return stable


async def _collect_manifest() -> dict[str, Any]:
    """Exercise production adaptation, wire SSE, and persistable projection together."""

    raw_events = _raw_events()
    agent = ProtocolAgent(raw_events)
    recorder = FakeArtifactRecorder()
    persisted_batches: list[list[dict[str, Any]]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted_batches.append(deepcopy(events))

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            [{"role": "user", "content": "주간 보고서를 만들어줘"}],
            {"configurable": {"thread_id": _THREAD_ID}},
            artifact_recorder=recorder,
            persist_callback=persist,
            run_id=_RUN_ID,
        )
    ]
    wire_events = [_stable_event(sse_payload(chunk), is_wire=True) for chunk in chunks]
    persisted_events = [
        _stable_event(event, is_wire=False) for batch in persisted_batches for event in batch
    ]
    adapted_events = [
        adapt_v3_protocol_event(raw, run_id=_RUN_ID, thread_id=_THREAD_ID) for raw in raw_events
    ]
    return {
        "agent_input": agent.inputs,
        "adapted_raw": adapted_events,
        "artifact_calls": [list(call) for call in recorder.calls],
        "event_order": [event["method"] for event in wire_events],
        "persistable": persisted_events,
        "wire_sse": wire_events,
    }


@pytest.mark.asyncio
async def test_runtime_wire_contract_matches_checked_raw_to_wire_fixture() -> None:
    """Given fixed v3 inputs, the persisted and SSE projections stay reviewed."""

    # Given: a production-stream manifest with fixed upstream IDs.
    manifest = await _collect_manifest()

    # When: every protocol boundary is observed through the same collector.
    # Then: stored adaptation, SSE wire, canonical input, and persisted data agree with review.
    assert_contract_matches("runtime_wire_v1.json", manifest)


@pytest.mark.asyncio
async def test_runtime_wire_contract_reports_precise_mutation_paths() -> None:
    """Given reviewed output, each boundary mutation reports its exact contract path."""

    # Given: the same independently collected boundary artifact as the golden test.
    expected = load_contract_fixture("runtime_wire_v1.json")
    manifest = await _collect_manifest()

    # When: a required wire field is removed.
    missing_field = deepcopy(manifest)
    del missing_field["wire_sse"][1]["method"]

    # Then: the validator identifies that public field rather than a generic mismatch.
    assert contract_diff_paths(expected, missing_field) == ["$.wire_sse[1].method"]

    # When: the recorded lifecycle/message order changes.
    reordered = deepcopy(manifest)
    reordered["event_order"][0], reordered["event_order"][1] = (
        reordered["event_order"][1],
        reordered["event_order"][0],
    )

    # Then: the two swapped positions are the complete order delta.
    assert contract_diff_paths(expected, reordered) == ["$.event_order[0]", "$.event_order[1]"]

    # When: the values checkpoint namespace diverges at the persisted boundary.
    checkpoint_mutation = deepcopy(manifest)
    values_index = next(
        index
        for index, event in enumerate(checkpoint_mutation["persistable"])
        if event["method"] == "values"
    )
    checkpoint_mutation["persistable"][values_index]["checkpoint_ns"] = "wrong-namespace"

    # Then: a replay-affecting checkpoint mutation identifies its exact stored path.
    assert contract_diff_paths(expected, checkpoint_mutation) == [
        f"$.persistable[{values_index}].checkpoint_ns"
    ]


def test_runtime_wire_contract_adapts_malformed_params_to_empty_custom_payload() -> None:
    """Malformed protocol params are normalized without leaking a raw exception."""

    # Given: the checked malformed raw record.
    raw = _raw_events()[-1]

    # When: the production adapter receives the malformed params field.
    event = adapt_v3_protocol_event(raw, run_id=_RUN_ID, thread_id=_THREAD_ID)

    # Then: it emits the standard named-custom envelope with a null payload.
    assert event["method"] == "custom"
    assert event["data"] == {"name": "malformed_payload", "payload": None}
