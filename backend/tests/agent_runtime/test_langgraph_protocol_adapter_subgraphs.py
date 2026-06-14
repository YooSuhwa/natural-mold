from __future__ import annotations

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_v3_protocol_event,
    extract_subagent_discovery,
)
from app.agent_runtime.protocol_events import matches_subscription


def test_subgraphs_alias_preserves_namespace_and_normalizes_subagent_discovery() -> None:
    event = adapt_v3_protocol_event(
        {
            "method": "subgraphs",
            "params": {
                "namespace": ["supervisor", "researcher"],
                "data": {
                    "id": "subgraph-1",
                    "graph_name": "researcher",
                    "state": "running",
                    "cause": {"type": "toolCall", "tool_call_id": "call-1"},
                    "input": "find docs",
                },
            },
            "seq": 11,
            "event_id": "evt-subgraphs-1",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "subgraphs"
    assert event["namespace"] == ["supervisor", "researcher"]
    assert event["upstream_event_id"] == "evt-subgraphs-1"
    assert matches_subscription(event, {"channels": ["subgraphs"]})
    assert extract_subagent_discovery(event) == {
        "id": "subgraph-1",
        "name": "researcher",
        "path": ["supervisor", "researcher"],
        "status": "running",
        "trigger_call_id": "call-1",
        "cause": {"type": "toolCall", "tool_call_id": "call-1"},
        "task_input": "find docs",
        "output": None,
        "error": None,
    }
