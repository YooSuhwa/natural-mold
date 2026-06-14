from __future__ import annotations

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_v3_protocol_event,
    synthesize_tool_events_from_values,
)


def test_synthesized_tool_finished_preserves_tool_message_name() -> None:
    values = adapt_v3_protocol_event(
        {
            "method": "values",
            "params": {
                "data": {
                    "messages": [
                        {
                            "type": "tool",
                            "name": "execute_in_skill",
                            "tool_call_id": "call-docx",
                            "content": "OUTPUT_FILES: moldy-langgraph-v3-report.md",
                            "status": "success",
                        }
                    ]
                }
            },
            "seq": 20,
            "event_id": "evt-20",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    events = synthesize_tool_events_from_values(values, first_seq=21)

    assert events[0]["data"] == {
        "event": "tool-finished",
        "tool_call_id": "call-docx",
        "name": "execute_in_skill",
        "content": "OUTPUT_FILES: moldy-langgraph-v3-report.md",
        "status": "success",
    }
    assert events[0]["seq"] == 21


def test_start_and_finish_for_same_tool_call_are_deduped_separately() -> None:
    started = adapt_v3_protocol_event(
        {
            "method": "values",
            "params": {
                "data": {
                    "messages": [
                        {
                            "type": "ai",
                            "tool_calls": [
                                {
                                    "id": "call-docx",
                                    "name": "execute_in_skill",
                                    "args": {"command": "node script.cjs"},
                                }
                            ],
                        }
                    ]
                }
            },
            "seq": 10,
            "event_id": "evt-10",
        },
        run_id="run-1",
        thread_id="thread-1",
    )
    finished = adapt_v3_protocol_event(
        {
            "method": "values",
            "params": {
                "data": {
                    "messages": [
                        {
                            "type": "tool",
                            "name": "execute_in_skill",
                            "tool_call_id": "call-docx",
                            "content": "OUTPUT_FILES: moldy-langgraph-v3-report.md",
                        }
                    ]
                }
            },
            "seq": 11,
            "event_id": "evt-11",
        },
        run_id="run-1",
        thread_id="thread-1",
    )
    seen: set[str] = set()

    start_events = synthesize_tool_events_from_values(
        started,
        seen_tool_call_ids=seen,
        first_seq=12,
    )
    finish_events = synthesize_tool_events_from_values(
        finished,
        seen_tool_call_ids=seen,
        first_seq=13,
    )

    assert start_events[0]["data"]["event"] == "tool-started"
    assert finish_events[0]["data"]["event"] == "tool-finished"
    assert finish_events[0]["data"]["name"] == "execute_in_skill"
    assert start_events[0]["seq"] == 12
    assert finish_events[0]["seq"] == 13
    assert seen == {"start:call-docx", "finish:call-docx"}
