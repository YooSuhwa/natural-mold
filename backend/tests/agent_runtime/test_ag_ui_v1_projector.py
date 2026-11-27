"""Read authorization and lifecycle invariants at the public stream boundary."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from ag_ui.core import BaseEvent, EventType

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility, ProjectionVisibilityChanged
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event

THREAD = "thread"
PHYSICAL = "physical"
CHILD = ("tools:1",)
HIDDEN = ("tools:2",)
POLICY = ChildVisibility(
    False, frozenset({"call"}), frozenset({CHILD}), frozenset({("call", CHILD, "task-id")})
)


def event(method: str, data: Any, namespace: tuple[str, ...] = ()) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=PHYSICAL,
        thread_id=THREAD,
        seq=1,
        method=method,
        data=data,
        namespace=list(namespace),
    )


def projector(*, policy: ChildVisibility = POLICY) -> AgUiV1Projector:
    return AgUiV1Projector(
        thread_id=THREAD,
        run_id="public",
        source_run_id=PHYSICAL,
        child_visibility=policy,
        defer_terminal=True,
    )


def wire(events: Sequence[BaseEvent]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in events]


def test_permissions_require_the_witnessed_call_and_exact_namespace() -> None:
    stream = projector()
    stream.project(
        event(
            "messages",
            {
                "id": "issuer",
                "type": "ai",
                "tool_calls": [
                    {
                        "name": "task",
                        "id": "call",
                        "args": {"description": "A", "subagent_type": "research"},
                    }
                ],
            },
        )
    )
    start = {"event": "started", "cause": {"type": "toolCall", "tool_call_id": "call"}}
    assert stream.project(event("lifecycle", {**start, "namespace": list(HIDDEN)})) == []
    assert stream.project(event("messages", {"type": "ai", "content": "PRIVATE"}, HIDDEN)) == []
    assert stream.owner_for(HIDDEN) is None
    assert stream.child_task_handles == {}
    assert (
        stream.project(
            event("values", {"todos": [{"content": "inspect", "status": "pending"}]}, CHILD)
        )
        == []
    )
    opened, todos = wire(stream.project(event("lifecycle", {**start, "namespace": list(CHILD)})))
    assert opened["type"] == "SUBAGENT_STARTED"
    assert opened["parentToolCallId"] == "call"
    assert opened["parentMessageId"] == "issuer"
    assert todos["activityType"] == "moldy.subagent_todos"
    owner = opened["subagentRunId"]
    assert todos["subagentRunId"] == owner == stream.owner_for(CHILD)
    assert stream.child_task_handles == {owner: "task-id"}
    assert (
        stream.project(event("custom:moldy.subagent_names", {"names": {"private": "SECRET"}})) == []
    )
    assert stream.project(event("lifecycle", {"event": "completed"})) == []
    terminal = wire(stream.finish("cancelled"))
    assert terminal[-2]["type"] == "SUBAGENT_ERROR"
    assert terminal[-2]["code"] == "CANCELLED"
    assert terminal[-1]["outcome"]["type"] == "cancelled"


def test_interrupted_child_status_and_unprovable_child_action() -> None:
    stream = projector()
    opened = wire(stream.project(event("subgraphs", {"state": "running", "path": list(CHILD)})))[-1]
    assert stream.child_task_handles == {}
    suspended = wire(
        stream.project(event("subgraphs", {"state": "requires_action", "path": list(CHILD)}))
    )
    assert suspended == [
        {
            "type": "SUBAGENT_FINISHED",
            "subagentRunId": opened["subagentRunId"],
            "outcome": {"type": "suspended", "interruptIds": []},
        }
    ]
    # One of the current slots belongs to a child no longer readable. LangGraph
    # may repeat the very same slot with its namespace omitted at the root.
    item = {
        "id": "hidden",
        "value": {"action_requests": [{"name": "private", "args": {"text": "SECRET"}}]},
    }
    stream.project(event("values", {"__interrupt__": [item]}, HIDDEN))
    final = wire(
        stream.finish(
            "interrupted",
            interrupts=[
                {"id": "visible", "ns": [], "value": {"question": "visible"}},
                {"id": "hidden", "ns": [], "value": item["value"]},
            ],
        )
    )
    assert final[-1]["type"] == "RUN_ERROR"
    assert "SECRET" not in json.dumps(final)
    assert "visible" not in json.dumps(final)


def test_authority_change_requires_full_rebuild_even_after_silent_rows() -> None:
    stream = projector()
    assert stream.project(event("values", {"files": {"internal": "SECRET"}}, HIDDEN)) == []
    with pytest.raises(ProjectionVisibilityChanged):
        stream.set_child_visibility(ChildVisibility(True, frozenset(), frozenset()))
    foreign = event("messages", {"type": "ai", "content": "wrong"})
    foreign["run_id"] = "foreign"
    with pytest.raises(ValueError, match="different run"):
        stream.project(foreign)


def test_hidden_or_unmeasured_child_does_not_create_partial_terminal_usage() -> None:
    stream = projector()
    stream.project(
        event(
            "messages",
            {
                "id": "m",
                "type": "ai",
                "content": "hi",
                "usage_metadata": {"input_tokens": 2, "output_tokens": 1},
            },
        )
    )
    stream.project(event("lifecycle", {"event": "running", "namespace": list(HIDDEN)}))
    assert "usage" not in wire(stream.finish("completed"))[-1]


def test_native_tool_only_message_start_finishes_metering_under_the_same_identity() -> None:
    stream = projector()
    stream.project(
        event("messages", {"event": "message-start", "role": "assistant", "id": "billed"})
    )
    stream.project(
        event(
            "messages",
            {"event": "message-finish", "usage": {"input_tokens": 8, "output_tokens": 3}},
        )
    )
    terminal = wire(stream.finish("completed"))[-1]
    assert terminal["usage"][0]["totalTokens"] == 11
    assert terminal["runId"] == "public"


def test_pristine_failure_and_duplicate_authoritative_terminal() -> None:
    stream = projector()
    assert stream.project(event("error", {"message": "PRIVATE"})) == []
    [first] = stream.finish("failed")
    assert first.type == EventType.RUN_ERROR
    assert "PRIVATE" not in first.model_dump_json()
    assert stream.start() == [] and stream.finish("failed") == []
