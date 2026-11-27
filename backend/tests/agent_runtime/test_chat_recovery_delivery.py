from __future__ import annotations

from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.recovery_stream import (
    RecoveryOutputStarted,
    RecoveryStreamCursor,
    continue_protocol_delivery,
)
from app.authz.org_context import OrgContext
from app.services.conversation_run_fence import RunFence, run_fence_scope
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload


def stored(seq: int, method: str, data: Any, event_id: str | None = None) -> dict[str, Any]:
    return dict(
        stored_protocol_event(
            run_id="r",
            thread_id="t",
            seq=seq,
            method=method,
            data=data,
            event_id=event_id,
        )
    )


def cursor(*events: dict[str, Any]) -> RecoveryStreamCursor:
    return RecoveryStreamCursor.from_events("r", "t", events)


def raw(method: str, data: Any, event_id: str | None = None) -> dict[str, Any]:
    return {
        "type": "event",
        "method": method,
        "params": {"namespace": [], "data": data},
        "seq": 0,
        "event_id": event_id,
    }


async def test_continued_stream_preserves_cursors_and_publishes_missing_tool_finish_only() -> None:
    start = {"event": "tool-started", "tool_call_id": "read", "tool_name": "lookup"}
    finish = {"event": "tool-finished", "tool_call_id": "read", "output": "result"}
    values = {"messages": [{"type": "human", "id": "ask"}, {"type": "ai", "id": "answer"}]}
    prefix = cursor(
        stored(0, "lifecycle", {"event": "running"}, "r:lifecycle:running"),
        stored(1, "custom", {"name": "moldy.subagent_names"}, "r:subagent_names"),
        stored(2, "values", values, "s1"),
        stored(3, "tools", start, "s2"),
    )
    assert prefix.visible_tool_call_ids == {"read"}
    with continue_protocol_delivery(prefix):
        chunks = [
            chunk
            async for chunk in stream_agent_response_langgraph(
                ProtocolAgent(
                    [
                        raw("values", values, "new-first-state"),
                        raw("tools", start, "s1"),
                        raw("tools", finish, "s2"),
                        raw("messages", {"chunk": "Done"}, "s1"),
                    ]
                ),
                None,
                {"configurable": {"thread_id": "t"}},
                subagent_display_names={"worker": "Worker"},
                run_id="r",
            )
        ]
    payload = [sse_payload(chunk) for chunk in chunks]
    assert [e["method"] for e in payload] == ["tools", "messages", "lifecycle"]
    assert [e["seq"] for e in payload] == [4, 5, 6]
    ids = [e["event_id"] for e in payload]
    assert len(set(ids)) == len(payload) and not (set(ids) & prefix.event_ids)
    assert payload[0]["params"]["data"] == finish


@pytest.mark.parametrize(
    ("method", "data"),
    [
        (
            "messages",
            {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "part"}},
        ),
        (
            "messages",
            {"event": "content-block-finish", "content": {"type": "text", "text": "part"}},
        ),
        ("custom", {"name": "file_event"}),
        ("tools", {"event": "tool-error", "tool_call_id": "uncertain"}),
        ("input.requested", {"interrupts": []}),
        ("lifecycle", {"event": "interrupted"}),
    ],
)
def test_partial_or_unrecognized_output_blocks_replay(method: str, data: Any) -> None:
    with pytest.raises(RecoveryOutputStarted):
        cursor(stored(1, method, data))


def test_visible_tool_call_requires_checkpoint_confirmation_even_before_tool_started() -> None:
    prefix = cursor(
        stored(
            1,
            "messages",
            {
                "event": "content-block-start",
                "content": {"type": "tool_call_chunk", "id": "seen-call"},
            },
        )
    )
    assert prefix.visible_tool_call_ids == {"seen-call"}


def fenced() -> RunFence:
    return RunFence(
        uuid4(),
        "worker",
        1,
        uuid4(),
        OrgContext(org_id=uuid4(), tenant_id=uuid4(), user_id=uuid4()),
        uuid4(),
    )


async def test_fenced_protocol_commits_before_publication_and_preserves_current_output_proof() -> (
    None
):
    stored_events: list[dict[str, Any]] = []
    broker = Mock()
    published: list[str] = []
    broker.publish_nowait.side_effect = lambda event: published.append(stored_events[-1]["id"])

    async def persist(events: list[dict[str, Any]]) -> None:
        stored_events.extend(events)

    history = [
        {"type": "ai", "id": "old", "content": "old reply"},
        {"type": "human", "id": "new", "content": "new question"},
    ]
    with run_fence_scope(fenced()):
        delivery = ProtocolEventDelivery(run_id="r", broker=broker, persist_callback=persist)
        for seq, messages in enumerate(
            (history, [*history, {"type": "ai", "id": "m", "content": "reply"}])
        ):
            await delivery.emit(
                stored_protocol_event(
                    run_id="r",
                    thread_id="t",
                    seq=seq,
                    method="values",
                    data={"messages": messages},
                )
            )
        await delivery.close()
    assert published == [e["id"] for e in stored_events]
    assert cursor(stored_events[0]).last_seq == 0
    assert "reply" not in str(stored_events[1]["data"])
    assert stored_events[1]["recovery_output_started"] is True
    with pytest.raises(RecoveryOutputStarted):
        cursor(*stored_events)


async def test_failed_durable_write_precedes_any_visible_event() -> None:
    broker = Mock()
    trace: list[dict[str, Any]] = []

    async def refused(_: list[dict[str, Any]]) -> None:
        raise RuntimeError("lost ownership")

    with run_fence_scope(fenced()):
        delivery = ProtocolEventDelivery(
            run_id="r", broker=broker, persist_callback=refused, trace_sink=trace
        )
        with pytest.raises(RuntimeError, match="lost ownership"):
            await delivery.emit(
                stored_protocol_event(
                    run_id="r",
                    thread_id="t",
                    seq=1,
                    method="values",
                    data={},
                )
            )
    broker.publish_nowait.assert_not_called()
    assert not trace and not delivery.emitted


async def test_native_tool_only_state_remains_recoverable_after_storage_compaction() -> None:
    call = {"type": "tool_call", "id": "read-1", "name": "lookup", "args": {}}
    history = [
        {"type": "human", "content": "ask"},
        {"type": "ai", "id": "m", "content": [call], "tool_calls": [call]},
        {"type": "tool", "tool_call_id": "read-1", "content": "result"},
    ]
    captured: list[dict[str, Any]] = []

    async def write(batch: list[dict[str, Any]]) -> None:
        captured.extend(batch)

    with run_fence_scope(fenced()):
        delivery = ProtocolEventDelivery(run_id="r", persist_callback=write)
        await delivery.emit(
            stored_protocol_event(
                run_id="r",
                thread_id="t",
                seq=0,
                method="values",
                data={"messages": history},
            )
        )
    assert cursor(*captured).visible_tool_call_ids == {"read-1"}
    assert "result" not in str(captured[0]["data"])
    mixed = {
        "type": "ai",
        "content": [call, {"type": "text", "text": "sent"}],
        "tool_calls": [call],
    }
    with pytest.raises(RecoveryOutputStarted):
        cursor(stored(1, "values", {"messages": [*history, mixed]}))


def test_usage_key_from_previous_generation_deduplicates_new_stream_usage() -> None:
    prefix = cursor(
        stored(
            1,
            "custom",
            {
                "name": "usage",
                "payload": {
                    "assistant_msg_id": "m",
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 3,
                },
            },
        )
    )
    assert prefix.usage_keys == {("m", 8, 2, 0, 3, None)}


async def test_recovered_values_deliver_unpublished_tool_result_once() -> None:
    from app.agent_runtime.run_metrics import RunMetricsAccumulator

    prefix = cursor(
        stored(0, "lifecycle", {"event": "running"}, "r:lifecycle:running"),
        stored(1, "tools", {"event": "tool-started", "tool_call_id": "read"}),
    )

    def pair(call_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "ai",
                "id": f"source-{call_id}",
                "tool_calls": [{"id": call_id, "name": "lookup", "args": {}}],
            },
            {"type": "tool", "tool_call_id": call_id, "name": "lookup", "content": "result"},
        ]

    snap = {"messages": [*pair("previous"), {"type": "human", "id": "current"}, *pair("read")]}
    metrics = RunMetricsAccumulator(
        started_at=None,
        baseline_completed_tool_call_source_identities=[((), "source-read", "read")],
    )

    with continue_protocol_delivery(prefix):
        out = [
            sse_payload(chunk)
            async for chunk in stream_agent_response_langgraph(
                ProtocolAgent(
                    [
                        raw("values", snap),
                        raw(
                            "tools",
                            {"event": "tool-finished", "tool_call_id": "read", "output": "result"},
                        ),
                    ]
                ),
                None,
                {"configurable": {"thread_id": "t"}},
                run_id="r",
                run_metrics=metrics,
            )
        ]
    tool_events = [event["params"]["data"] for event in out if event["method"] == "tools"]
    assert [(d["event"], d["tool_call_id"]) for d in tool_events] == [("tool-finished", "read")]
