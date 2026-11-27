from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_recovery import restore_recovery_metrics


def event(method: str, data: dict[str, Any]) -> StoredProtocolEvent:
    return stored_protocol_event(run_id="run", thread_id="thread", seq=0, method=method, data=data)


def usage(message_id: str, prompt: int, completion: int) -> StoredProtocolEvent:
    return event(
        "custom",
        {
            "name": "usage",
            "payload": {
                "assistant_msg_id": message_id,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "estimated_cost": 0.015,
            },
        },
    )


def test_recovery_restores_only_current_measured_turn_and_deduplicates_continuation() -> None:
    old = AIMessage(
        id="previous",
        content="older answer",
        usage_metadata={"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    )
    before_crash = AIMessage(
        id="current",
        content="",
        tool_calls=[{"id": "lookup", "name": "lookup", "args": {}}],
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    )
    started = event("tools", {"event": "tool-started", "tool_call_id": "lookup", "name": "lookup"})
    metrics = RunMetricsAccumulator(started_at=None, complete_event_capture=False)
    restore_recovery_metrics(
        metrics,
        run_id="run",
        thread_id="thread",
        messages=[
            old,
            HumanMessage(id="verified-user-turn", content="new request"),
            before_crash,
            ToolMessage(id="result", content="done", tool_call_id="lookup"),
            HumanMessage(id="verified-steer", content="include sources"),
        ],
        turn_start_index=1,
        events=[
            dict(usage("previous", 50, 20)),
            dict(usage("unattributed", 70, 35)),
            dict(started),
            dict(usage("current", 9, 2)),
        ],
    )
    metrics.observe(event("values", {"messages": [old.model_dump(), before_crash.model_dump()]}))
    metrics.observe(started)
    metrics.start_model_generation("after-recovery")
    metrics.observe(usage("new-model", 7, 3))
    metrics.finish_model_generation("after-recovery")
    result = metrics.finalize("completed")
    assert (result.prompt_tokens, result.completion_tokens) == (17, 5)
    assert result.estimated_cost == 0.03
    assert not result.usage_complete
    assert (
        result.elapsed_ms
        is result.ttft_ms
        is result.generation_ms
        is result.tokens_per_second
        is None
    )
    assert result.root_tool_calls is None
    assert [(item.kind, item.call_id, item.elapsed_ms) for item in result.activity] == [
        ("tool_call", "lookup", None),
        ("model_usage", "current", None),
        ("model_usage", "new-model", None),
    ]


def test_recovery_without_measured_usage_preserves_unknowns() -> None:
    metrics = RunMetricsAccumulator(started_at=None, complete_event_capture=False)
    restore_recovery_metrics(
        metrics,
        run_id="run",
        thread_id="thread",
        messages=[HumanMessage(id="verified-user-turn", content="request")],
        events=[],
    )
    result = metrics.finalize("failed")
    assert result.prompt_tokens is result.completion_tokens is result.estimated_cost is None
    assert result.usage_complete is False
