"""Behavior coverage for the persistence-independent run metrics collector."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.agent_runtime.langgraph_protocol_adapter import adapt_v3_protocol_event
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator


class FakeMonotonicClock:
    """Small deterministic monotonic clock used by real event replay tests."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _message_event(
    *,
    seq: int,
    message_id: str,
    usage: dict[str, int | float] | None = None,
    content: str = "",
    namespace: list[str] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {"id": message_id, "type": "ai", "content": content}
    if usage is not None:
        message["usage_metadata"] = usage
    return stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=seq,
        method="messages",
        namespace=namespace,
        data=message,
    )


def _tool_event(
    *,
    seq: int,
    call_id: str,
    name: str,
    namespace: list[str] | None = None,
) -> dict[str, object]:
    return stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=seq,
        method="tools",
        namespace=namespace,
        data={"event": "tool-started", "tool_call_id": call_id, "name": name},
    )


def test_accumulates_cumulative_model_usage_without_replay_inflation() -> None:
    # Given: two real messages events, including a repeated cumulative model snapshot.
    clock = FakeMonotonicClock(12.0)
    accumulator = RunMetricsAccumulator(started_at=10.0, monotonic=clock)
    accumulator.start_model_generation()

    # When: the stored protocol stream is replayed through the accumulator.
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-1",
            content="first token",
            usage={"input_tokens": 10, "output_tokens": 3, "estimated_cost": 0.10},
        )
    )
    accumulator.observe(
        _message_event(
            seq=2,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 3, "estimated_cost": 0.10},
        )
    )
    accumulator.observe(
        _message_event(
            seq=3,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 5, "estimated_cost": 0.13},
        )
    )
    accumulator.observe(
        _message_event(
            seq=3,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 3, "estimated_cost": 0.10},
        )
    )
    accumulator.observe(
        _message_event(
            seq=4,
            message_id="assistant-2",
            usage={"input_tokens": 20, "output_tokens": 7, "estimated_cost": 0.20},
        )
    )
    clock.now = 20.0
    accumulator.finish_model_generation()
    snapshot = accumulator.finalize("completed")

    # Then: totals are the latest cumulative value for each model message, not sum of replays.
    assert snapshot.prompt_tokens == 30
    assert snapshot.completion_tokens == 12
    assert snapshot.estimated_cost == 0.33
    assert snapshot.elapsed_ms == 10_000.0
    assert snapshot.ttft_ms == 2_000.0
    assert snapshot.generation_ms == 8_000.0
    assert snapshot.tokens_per_second == 1.5


def test_measures_active_model_generation_without_counting_tool_waits() -> None:
    # Given: two model intervals separated by a long tool wait.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)

    # When: the lifecycle integration marks actual model start/finish boundaries.
    accumulator.start_model_generation()
    clock.now = 2.0
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-1",
            content="first token",
            usage={"input_tokens": 10, "output_tokens": 4},
        )
    )
    clock.now = 3.0
    accumulator.finish_model_generation()
    clock.now = 13.0
    accumulator.observe(_tool_event(seq=2, call_id="search", name="search"))
    clock.now = 14.0
    accumulator.start_model_generation()
    clock.now = 15.0
    accumulator.observe(
        _message_event(
            seq=3,
            message_id="assistant-2",
            content="second token",
            usage={"input_tokens": 10, "output_tokens": 6},
        )
    )
    clock.now = 16.0
    accumulator.finish_model_generation()
    clock.now = 20.0
    snapshot = accumulator.finalize("completed")

    # Then: wall duration retains the wait, while generation/TPS use only model intervals.
    assert snapshot.elapsed_ms == 20_000.0
    assert snapshot.ttft_ms == 2_000.0
    assert snapshot.generation_ms == 4_000.0
    assert snapshot.tokens_per_second == 2.5


def test_preserves_known_usage_when_later_model_message_is_canceled_without_usage() -> None:
    # Given: one model response has provider usage before a second response starts.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-complete",
            content="measured",
            usage={"input_tokens": 8, "output_tokens": 4},
        )
    )
    accumulator.observe(
        _message_event(
            seq=2,
            message_id="assistant-canceled",
            content="partial",
        )
    )

    # When: the run is canceled before the second provider usage snapshot arrives.
    snapshot = accumulator.finalize("canceled")

    # Then: known usage survives, while its incomplete coverage is explicit.
    assert snapshot.prompt_tokens == 8
    assert snapshot.completion_tokens == 4
    assert snapshot.usage_complete is False


def test_preserves_generation_duration_when_failure_precedes_first_token() -> None:
    # Given: a provider call begins but fails before producing output content.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    accumulator.start_model_generation("failed-model")
    clock.now = 3.0
    accumulator.finish_model_generation("failed-model")

    # When: failure metrics are finalized.
    snapshot = accumulator.finalize("failed")

    # Then: model time is known, while first-token and throughput remain unknown.
    assert snapshot.generation_ms == 2_000.0
    assert snapshot.ttft_ms is None
    assert snapshot.tokens_per_second is None
    assert snapshot.usage_complete is False


def test_empty_provider_usage_remains_unknown_and_incomplete() -> None:
    # Given: a model invocation emits content but only an empty provider usage mapping.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    accumulator.start_model_generation("model-empty-usage")
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-empty-usage",
            content="answer",
            usage={},
        )
    )
    clock.now = 2.0
    accumulator.finish_model_generation("model-empty-usage")

    # When: the run finalizes without a real token report.
    snapshot = accumulator.finalize("completed")

    # Then: no token zero is invented and coverage is explicitly incomplete.
    assert snapshot.prompt_tokens is None
    assert snapshot.completion_tokens is None
    assert snapshot.cache_creation_tokens is None
    assert snapshot.cache_read_tokens is None
    assert snapshot.estimated_cost is None
    assert snapshot.usage_complete is False


def test_freezes_terminal_snapshot_and_ignores_late_protocol_events() -> None:
    # Given: a run with measured usage reaches a terminal state.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
    )
    clock.now = 5.0
    frozen = accumulator.finalize("completed")

    # When: time advances and both a late replay and additional apparent usage arrive.
    clock.now = 30.0
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
    )
    accumulator.observe(
        _message_event(
            seq=2,
            message_id="assistant-2",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
    )

    # Then: terminal metrics and activity cannot drift after finalization.
    assert accumulator.snapshot() == frozen
    assert frozen.elapsed_ms == 5_000.0
    assert frozen.completion_tokens == 5


def test_excludes_baseline_checkpoint_messages_but_keeps_current_final_only_usage() -> None:
    # Given: a values snapshot contains a checkpoint message and this run's final-only message.
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        method="values",
        data={
            "messages": [
                {
                    "id": "before-run",
                    "type": "ai",
                    "usage_metadata": {"input_tokens": 90, "output_tokens": 45},
                },
                {
                    "id": "current-final",
                    "type": "ai",
                    "usage_metadata": {"input_tokens": 10, "output_tokens": 5},
                },
            ]
        },
    )
    accumulator = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=FakeMonotonicClock(1.0),
        baseline_message_identities={((), "before-run")},
    )

    # When: the full checkpoint values event is consumed.
    accumulator.observe(event)
    snapshot = accumulator.finalize("completed")

    # Then: only current-run final usage enters the run total.
    assert snapshot.prompt_tokens == 10
    assert snapshot.completion_tokens == 5


def test_compacts_activity_to_one_model_entry_per_message_and_a_bounded_tail() -> None:
    # Given: repeated cumulative model snapshots plus more activity than the retention cap.
    accumulator = RunMetricsAccumulator(
        started_at=0.0,
        monotonic=FakeMonotonicClock(1.0),
        activity_limit=2,
    )

    # When: actual events are consumed.
    accumulator.observe(
        _message_event(
            seq=1,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 1},
        )
    )
    accumulator.observe(
        _message_event(
            seq=2,
            message_id="assistant-1",
            usage={"input_tokens": 10, "output_tokens": 2},
        )
    )
    accumulator.observe(_tool_event(seq=3, call_id="tool-1", name="search"))
    accumulator.observe(_tool_event(seq=4, call_id="tool-2", name="fetch"))
    snapshot = accumulator.finalize("completed")

    # Then: usage snapshots collapse to one activity and the retained timeline is bounded.
    assert snapshot.activity_truncated is True
    assert [activity.call_id for activity in snapshot.activity] == ["tool-1", "tool-2"]


def test_tracks_tool_and_subagent_calls_by_namespace_and_call_id() -> None:
    # Given: root and descendant calls reuse IDs, and task discovery repeats its triggering call.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)

    # When: synthesized protocol tool/task events arrive, including replayed starts.
    accumulator.observe(_tool_event(seq=1, call_id="shared", name="search"))
    accumulator.observe(_tool_event(seq=2, call_id="shared", name="search"))
    accumulator.observe(
        _tool_event(seq=3, call_id="shared", name="search", namespace=["task:research"])
    )
    accumulator.observe(_tool_event(seq=4, call_id="task-1", name="task"))
    accumulator.observe(
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=5,
            method="tasks",
            data={"trigger_call_id": "task-1", "name": "research"},
        )
    )
    accumulator.observe(
        _tool_event(seq=6, call_id="task-1", name="task", namespace=["task:research"])
    )
    snapshot = accumulator.finalize("completed")

    # Then: namespace prevents collisions, while task discovery does not double-count its call.
    assert snapshot.root_tool_calls == 2
    assert snapshot.descendant_tool_calls == 2
    assert snapshot.root_subagent_calls == 1
    assert snapshot.descendant_subagent_calls == 1


def test_reads_usage_nested_in_v3_messages_stream_metadata() -> None:
    # Given: the actual v3 adapter shape where metadata is nested under the message envelope.
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "messages",
            "params": {
                "data": (
                    AIMessage(content="hi", id="assistant-1"),
                    {
                        "langgraph_node": "model",
                        "usage_metadata": {"input_tokens": 30, "output_tokens": 12},
                    },
                ),
            },
            "seq": 4,
        },
        run_id="run-1",
        thread_id="thread-1",
    )
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=FakeMonotonicClock(1.0))

    # When: that adapted event reaches the collector.
    accumulator.observe(event)
    snapshot = accumulator.finalize("completed")

    # Then: the parent assistant message ID binds the nested cumulative usage.
    assert snapshot.prompt_tokens == 30
    assert snapshot.completion_tokens == 12


def test_records_canceled_partial_run_without_inventing_first_token_or_usage() -> None:
    # Given: a run is canceled before any model event or first content token.
    clock = FakeMonotonicClock(9.0)
    accumulator = RunMetricsAccumulator(started_at=5.0, monotonic=clock)

    # When: its terminal state is recorded.
    snapshot = accumulator.finalize("canceled")

    # Then: known event counts remain zero, while provider/timing measurements remain unavailable.
    assert snapshot.terminal_state == "canceled"
    assert snapshot.elapsed_ms == 4_000.0
    assert snapshot.prompt_tokens is None
    assert snapshot.completion_tokens is None
    assert snapshot.estimated_cost is None
    assert snapshot.ttft_ms is None
    assert snapshot.generation_ms is None
    assert snapshot.tokens_per_second is None
    assert snapshot.root_tool_calls == 0
    assert snapshot.descendant_tool_calls == 0


def test_keeps_historical_metrics_unknown_when_complete_event_capture_is_unavailable() -> None:
    # Given: an older run has terminal status but never had a complete protocol capture.
    accumulator = RunMetricsAccumulator(
        started_at=None,
        monotonic=FakeMonotonicClock(50.0),
        complete_event_capture=False,
    )

    # When: the historical terminal state is represented for persistence.
    snapshot = accumulator.finalize("failed")

    # Then: no absent measurement is represented as a zero.
    assert snapshot.terminal_state == "failed"
    assert snapshot.elapsed_ms is None
    assert snapshot.prompt_tokens is None
    assert snapshot.root_tool_calls is None
    assert snapshot.descendant_subagent_calls is None
