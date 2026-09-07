from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_v3_protocol_event,
    synthesize_tool_events_from_values,
)
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_baseline import (
    baseline_completed_tool_call_source_identities,
    baseline_message_identities,
)
from app.agent_runtime.run_metrics_types import RunMetricsSnapshot, TerminalRunState
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent


def _values_event(
    messages: list[BaseMessage],
    *,
    namespace: list[str] | None = None,
) -> StoredProtocolEvent:
    return adapt_v3_protocol_event(
        {
            "method": "values",
            "params": {"namespace": namespace or [], "data": {"messages": messages}},
            "seq": 1,
            "event_id": "values-current",
        },
        run_id="run-current",
        thread_id="thread-shared",
    )


def _observe_values(
    event: StoredProtocolEvent,
    *,
    historical_messages: list[BaseMessage],
    terminal_state: TerminalRunState = "completed",
) -> RunMetricsSnapshot:
    accumulator = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=lambda: 1.0,
        baseline_message_identities=baseline_message_identities(historical_messages),
        baseline_completed_tool_call_source_identities=(
            baseline_completed_tool_call_source_identities(historical_messages)
        ),
    )
    accumulator.observe(event)
    for tool_event in synthesize_tool_events_from_values(
        event,
        baseline_completed_tool_call_source_identities=(
            accumulator.baseline_completed_tool_call_source_identities
        ),
    ):
        accumulator.observe(tool_event)
    return accumulator.finalize(terminal_state)


def test_excludes_completed_historical_tools_from_next_text_only_canceled_run() -> None:
    # Given: the next run's first cumulative values state replays three completed tools.
    historical_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-old-task",
            tool_calls=[{"id": "call-old-task", "name": "task", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-old-task", name="task"),
        AIMessage(
            content="",
            id="assistant-old-search-1",
            tool_calls=[{"id": "call-old-search-1", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-old-search-1", name="search"),
        AIMessage(
            content="",
            id="assistant-old-search-2",
            tool_calls=[{"id": "call-old-search-2", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-old-search-2", name="search"),
    ]
    event = _values_event(
        [*historical_messages, HumanMessage(content="new text-only run", id="human-current")]
    )

    # When: the real adapter output and its synthesized tool events reach metrics.
    snapshot = _observe_values(
        event,
        historical_messages=historical_messages,
        terminal_state="canceled",
    )

    # Then: completed checkpoint activity is not charged to the text-only run.
    assert snapshot.root_tool_calls == 0
    assert snapshot.root_subagent_calls == 0


def test_counts_current_assistant_that_reuses_completed_historical_tool_id() -> None:
    # Given: a new assistant reuses a fixed scripted call ID from completed history.
    historical_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-old",
            tool_calls=[{"id": "call-old", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-old", name="search"),
    ]
    event = _values_event(
        [
            *historical_messages,
            AIMessage(
                content="",
                id="assistant-current",
                tool_calls=[{"id": "call-old", "name": "search", "args": {}}],
            ),
            ToolMessage(content="current done", tool_call_id="call-old", name="search"),
        ]
    )

    # When: cumulative values are adapted and synthesized.
    baseline = baseline_completed_tool_call_source_identities(historical_messages)
    tool_events = synthesize_tool_events_from_values(
        event,
        baseline_completed_tool_call_source_identities=baseline,
    )
    accumulator = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=lambda: 1.0,
        baseline_completed_tool_call_source_identities=baseline,
    )
    accumulator.observe(event)
    for tool_event in tool_events:
        accumulator.observe(tool_event)
    snapshot = accumulator.finalize("completed")

    # Then: only this run's complete lifecycle is emitted and counted.
    assert [event["data"]["event"] for event in tool_events] == [
        "tool-started",
        "tool-finished",
    ]
    assert tool_events[1]["data"]["status"] == "complete"
    assert snapshot.root_tool_calls == 1
    assert snapshot.root_subagent_calls == 0


def test_counts_pending_checkpoint_tool_that_has_no_completed_result() -> None:
    # Given: a HiTL task call is pending at the pre-input checkpoint.
    pending_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-completed",
            tool_calls=[{"id": "call-fixed", "name": "task", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-fixed", name="task"),
        AIMessage(
            content="",
            id="assistant-pending",
            tool_calls=[{"id": "call-fixed", "name": "task", "args": {}}],
        ),
    ]
    event = _values_event(pending_messages)

    # When: the resumed run observes the still-pending call from cumulative values.
    snapshot = _observe_values(event, historical_messages=pending_messages)

    # Then: the incomplete call remains current work and counts as tool plus subagent.
    assert snapshot.root_tool_calls == 1
    assert snapshot.root_subagent_calls == 1


def test_preserves_explicit_current_tool_event_with_completed_historical_call_id() -> None:
    # Given: a historical result shares an ID with an explicit current tool event.
    historical_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-old",
            tool_calls=[{"id": "call-shared", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-shared", name="search"),
    ]
    accumulator = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=lambda: 1.0,
        baseline_completed_tool_call_source_identities=(
            baseline_completed_tool_call_source_identities(historical_messages)
        ),
    )

    # When: the runtime emits a direct current-run event rather than values synthesis.
    accumulator.observe(
        stored_protocol_event(
            run_id="run-current",
            thread_id="thread-shared",
            seq=1,
            method="tools",
            data={"event": "tool-started", "tool_call_id": "call-shared", "name": "search"},
        )
    )
    snapshot = accumulator.finalize("completed")

    # Then: baseline filtering does not suppress explicit current execution.
    assert snapshot.root_tool_calls == 1


def test_completed_root_identity_does_not_suppress_same_id_in_descendant_namespace() -> None:
    # Given: only the root identity is completed before the run.
    historical_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-shared",
            tool_calls=[{"id": "call-shared", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-shared", name="search"),
    ]
    event = _values_event(
        [
            AIMessage(
                content="",
                id="assistant-shared",
                tool_calls=[{"id": "call-shared", "name": "search", "args": {}}],
            )
        ],
        namespace=["task:current"],
    )

    # When: a descendant emits the same opaque call ID.
    snapshot = _observe_values(event, historical_messages=historical_messages)

    # Then: namespace remains part of the identity.
    assert snapshot.root_tool_calls == 0
    assert snapshot.descendant_tool_calls == 1


@pytest.mark.asyncio
async def test_stream_wires_completed_history_baseline_into_values_synthesis() -> None:
    # Given: a real v3 stream replays one completed historical tool into a text-only run.
    historical_messages: list[BaseMessage] = [
        AIMessage(
            content="",
            id="assistant-old",
            tool_calls=[{"id": "call-old", "name": "search", "args": {}}],
        ),
        ToolMessage(content="done", tool_call_id="call-old", name="search"),
    ]
    metrics = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=lambda: 1.0,
        baseline_completed_tool_call_source_identities=(
            baseline_completed_tool_call_source_identities(historical_messages)
        ),
    )
    agent = ProtocolAgent(
        [
            {
                "method": "values",
                "params": {
                    "data": {
                        "messages": [
                            *historical_messages,
                            HumanMessage(content="current text", id="human-current"),
                        ]
                    }
                },
                "seq": 1,
                "event_id": "values-stream",
            }
        ]
    )

    # When: the production stream integration adapts, synthesizes, and observes the event.
    _chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-shared"}},
            run_id="run-current",
            run_metrics=metrics,
        )
    ]
    snapshot = metrics.finalize("canceled")

    # Then: the stream-level wiring leaves the new text-only run at zero activity.
    assert snapshot.root_tool_calls == 0
    assert snapshot.root_subagent_calls == 0
