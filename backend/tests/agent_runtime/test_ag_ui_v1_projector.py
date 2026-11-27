"""Read authorization and lifecycle invariants at the public stream boundary."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
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
    issued = stream.project(
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
    assert opened["parentToolCallId"] == next(
        frame["toolCallId"] for frame in wire(issued) if frame["type"] == "TOOL_CALL_START"
    )
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
    [visible] = final[-1]["outcome"]["interrupts"]
    assert visible["id"] == "visible"
    assert visible["metadata"]["moldy"]["actionable"] is False
    assert "responseSchema" not in visible
    assert "SECRET" not in json.dumps(final)
    assert "hidden" not in json.dumps(final)


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


def test_reused_parent_call_id_cannot_disclose_a_nested_unauthorized_delegation() -> None:
    stream = projector()
    call = {"name": "task", "id": "call", "args": {"description": "allowed"}}
    issued = wire(
        stream.project(event("messages", {"id": "issuer", "type": "ai", "tool_calls": [call]}))
    )
    assert any(item["type"] == "TOOL_CALL_START" for item in issued)
    stream.project(
        event(
            "lifecycle",
            {
                "event": "started",
                "namespace": list(CHILD),
                "cause": {"type": "toolCall", "tool_call_id": "call"},
            },
        )
    )
    reused = {**call, "args": {"description": "REVOKED_CHILD_BRIEF"}}
    same_id = stream.project(
        event("messages", {"id": "other", "type": "ai", "tool_calls": [reused]}, CHILD)
    )
    assert same_id == []
    assert (
        stream.project(
            event(
                "tools",
                {
                    "event": "tool-finished",
                    "tool_call_id": "call",
                    "name": "task",
                    "content": "REVOKED_CHILD_OUTPUT",
                },
                CHILD,
            )
        )
        == []
    )


def test_structural_graph_path_is_not_an_issuing_child() -> None:
    ns = ("tools:parent", "task:research")
    stream = projector(
        policy=ChildVisibility(
            False, frozenset({"c"}), frozenset({ns}), frozenset({("c", ns, "task-row")})
        )
    )
    frames = wire(
        stream.project(
            event(
                "messages",
                {
                    "id": "m",
                    "type": "ai",
                    "tool_calls": [
                        {"id": "c", "name": "task", "args": {"subagent_type": "research"}}
                    ],
                },
            )
        )
    )
    call = next(f["toolCallId"] for f in frames if f["type"] == "TOOL_CALL_START")
    [started] = wire(
        stream.project(
            event(
                "lifecycle",
                {
                    "event": "started",
                    "namespace": list(ns),
                    "cause": {"type": "toolCall", "tool_call_id": "c"},
                },
            )
        )
    )
    assert started["parentToolCallId"] == call
    assert stream.child_task_handles == {started["subagentRunId"]: "task-row"}


@pytest.mark.parametrize("legacy", [True, False])
def test_checkpoint_only_child_receives_lifecycle_and_authoritative_expiry(legacy: bool) -> None:
    stream = projector(
        policy=ChildVisibility(legacy, POLICY.tool_call_ids, POLICY.namespaces, POLICY.bindings)
    )
    result = wire(
        stream.finish(
            "interrupted",
            interrupts=[
                {
                    "id": "verified",
                    "ns": list(CHILD),
                    "value": {"type": "ask_user", "question": "Continue?"},
                }
            ],
            expires_at={"verified": "2026-11-28T14:00:00Z"},
            legacy_child_ids={"verified"},
        )
    )
    assert [item["type"] for item in result] == [
        "RUN_STARTED",
        "SUBAGENT_STARTED",
        "SUBAGENT_FINISHED",
        "RUN_FINISHED",
    ]
    [slot] = result[-1]["outcome"]["interrupts"]
    assert slot["subagentRunId"] == result[1]["subagentRunId"]
    assert result[2]["outcome"]["interruptIds"] == ["verified"]
    assert slot["expiresAt"] == "2026-11-28T14:00:00Z"
    assert bool(slot["metadata"]["moldy"].get("legacyInterruptAccess")) is legacy


def test_archived_delegation_restores_its_issued_call_without_a_child_task_handle() -> None:
    fixture = Path(__file__).parent / "fixtures/delegated_chat_0_7_32.jsonl"
    rows: list[StoredProtocolEvent] = [
        json.loads(line) for line in fixture.read_text().splitlines()
    ]
    ns = next(tuple(row["namespace"]) for row in rows if row["namespace"])
    raw_call = "call-research"
    policy = ChildVisibility(
        False,
        frozenset({raw_call}),
        frozenset({ns}),
        historical_bindings=frozenset({(raw_call, ns)}),
    )
    stream = AgUiV1Projector(
        thread_id=rows[0]["thread_id"], run_id=rows[0]["run_id"], child_visibility=policy
    )
    frames = wire([frame for row in rows for frame in stream.project(row)])
    [call] = [f for f in frames if f["type"] == "TOOL_CALL_START"]
    [child] = [f for f in frames if f["type"] == "SUBAGENT_STARTED"]
    [result] = [f for f in frames if f["type"] == "TOOL_CALL_RESULT"]
    assert child["parentToolCallId"] == result["toolCallId"] == call["toolCallId"]
    assert child["parentMessageId"] == call["parentMessageId"]
    assert child["subagentRunId"] == stream.owner_for(ns)
    assert stream.owner_for((*ns, "other-child")) is None
    assert stream.child_task_handles == {}
    assert "legacyInterruptAccess" not in json.dumps(frames)


def test_ambiguous_historical_call_and_current_slot_never_create_actions() -> None:
    policy = ChildVisibility(
        False,
        frozenset({"call"}),
        frozenset({CHILD, HIDDEN}),
        historical_bindings=frozenset({("call", CHILD), ("call", HIDDEN)}),
    )
    stream = projector(policy=policy)
    assert (
        stream.project(
            event(
                "messages",
                {
                    "id": "issuer",
                    "type": "ai",
                    "tool_calls": [
                        {"id": "call", "name": "task", "args": {"description": "private"}}
                    ],
                },
            )
        )
        == []
    )
    [_, started] = wire(
        stream.project(
            event(
                "lifecycle",
                {
                    "event": "started",
                    "namespace": list(CHILD),
                    "cause": {"type": "toolCall", "tool_call_id": "call"},
                },
            )
        )
    )
    assert "parentToolCallId" not in started
    assert stream.child_task_handles == {}
    approval = {"type": "ask_user", "question": "Old private request?"}
    # The original journal can repeat this slot on a root values frame first.
    stream.project(event("values", {"__interrupt__": [{"id": "spent-slot", "value": approval}]}))
    stream.project(
        event("input.requested", {"interrupt_id": "spent-slot", "payload": approval}, CHILD)
    )
    [closed] = wire(
        stream.project(event("lifecycle", {"event": "interrupted", "namespace": list(CHILD)}))
    )
    assert closed["outcome"] == {"type": "suspended"}
    final = wire(
        stream.finish(
            "interrupted",
            interrupts=[{"id": "spent-slot", "ns": [], "value": approval}],
            legacy_child_ids={"spent-slot"},
        )
    )
    assert final[-1]["code"] == "pending_input_unavailable"
    assert "spent-slot" not in json.dumps(final)
    assert "Old private request" not in json.dumps(final)


def test_only_current_mcp_binding_can_expose_raw_widget_reference() -> None:
    run = "7c3a2eee-4d19-44dc-861a-3796b3689bf6"
    binding = "acd09417-9bb7-4dff-b6c7-31a23b24f888"
    call = "provider/call:one"
    sources = [
        stored_protocol_event(
            run_id=run,
            thread_id=THREAD,
            seq=0,
            method="messages",
            data={
                "type": "ai",
                "id": "source-assistant",
                "tool_calls": [{"id": call, "name": "mcp_report", "args": {}}],
            },
        ),
        stored_protocol_event(
            run_id=run,
            thread_id=THREAD,
            seq=1,
            method="messages",
            data={
                "type": "tool",
                "tool_call_id": call,
                "name": "mcp_report",
                "content": "Public result",
                "artifact": {
                    "mcp_app": {
                        "version": 1,
                        "binding_id": binding,
                        "run_id": run,
                        "resource_uri": "ui://private/resource",
                        "result_meta": {"privateField": "private-description"},
                    }
                },
            },
        ),
    ]

    def replay(allowed: frozenset[tuple[str, str]] | None) -> list[dict[str, Any]]:
        render = AgUiV1Projector(
            thread_id=THREAD, run_id="public", source_run_id=run, readable_widget_bindings=allowed
        )
        return wire([frame for row in sources for frame in render.project(row)])

    positive = replay(frozenset({(call, binding)}))
    [pointer] = [f for f in positive if f["type"] == "ACTIVITY_SNAPSHOT"]
    assert pointer["content"]["binding_id"] == binding
    assert pointer["content"]["tool_call_id"] == call
    for allowed in (
        None,
        frozenset(),
        frozenset({("other-call", binding)}),
        frozenset({(call, run)}),
    ):
        denied = replay(allowed)
        assert any(
            f["type"] == "TOOL_CALL_RESULT" and f["content"] == "Public result" for f in denied
        )
        serialized = json.dumps(denied)
        assert all(
            token not in serialized
            for token in (call, binding, "ui://", "private-description", "moldy.mcp_app")
        )
