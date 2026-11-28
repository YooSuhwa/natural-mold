import pytest

from app.services.ag_ui_snapshot_projection import AgUiRunSnapshot


def test_parallel_children_and_late_results_match_sdk_order() -> None:
    fold = AgUiRunSnapshot()
    for child in ("A", "B"):
        fold.apply(
            {
                "type": "TOOL_CALL_START",
                "parentMessageId": "parent",
                "toolCallId": child,
                "toolCallName": "task",
            }
        )
        fold.apply({"type": "TOOL_CALL_ARGS", "toolCallId": child, "delta": "{}"})
        fold.apply(
            {
                "type": "SUBAGENT_STARTED",
                "subagentRunId": child,
                "name": "research",
                "parentToolCallId": child,
            }
        )
    for child in ("B", "A"):
        mid = f"reply-{child}"
        fold.apply({"type": "TEXT_MESSAGE_START", "messageId": mid, "subagentRunId": child})
        fold.apply(
            {
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": mid,
                "delta": child,
                "subagentRunId": child,
            }
        )
        fold.apply({"type": "SUBAGENT_FINISHED", "subagentRunId": child, "result": child})
        fold.apply(
            {
                "type": "TOOL_CALL_RESULT",
                "messageId": f"result-{child}",
                "toolCallId": child,
                "content": child,
            }
        )
    fold.apply(
        {
            "type": "TOOL_CALL_RESULT",
            "messageId": "result-B",
            "toolCallId": "B",
            "content": "corrected",
        }
    )
    rows = fold.messages()
    assert [row["id"] for row in rows] == ["parent", "result-B", "result-A", "reply-B", "reply-A"]
    assert rows[0] == {
        "id": "parent",
        "role": "assistant",
        "toolCalls": [
            {"id": cid, "type": "function", "function": {"name": "task", "arguments": "{}"}}
            for cid in ("A", "B")
        ],
    }
    assert rows[1]["content"] == "corrected"
    assert rows[3] == {"id": "reply-B", "role": "assistant", "content": "B", "subagentRunId": "B"}
    assert fold.links_by_id()["reply-A"] == {
        "subagentRunId": "A",
        "parentMessageId": "parent",
        "parentToolCallId": "A",
    }
    for child in fold.subagents():
        assert child == {
            "subagentRunId": child["subagentRunId"],
            "parentMessageId": "parent",
            "parentToolCallId": child["subagentRunId"],
            "name": "research",
            "status": "completed",
            "result": child["subagentRunId"],
        }


def test_suspended_nested_child_retains_actual_ids_and_safe_results() -> None:
    fold = AgUiRunSnapshot()
    fold.apply({"type": "SUBAGENT_STARTED", "subagentRunId": "parent", "name": "reviewer"})
    fold.apply(
        {
            "type": "TOOL_CALL_START",
            "subagentRunId": "parent",
            "parentMessageId": "p",
            "toolCallId": "task",
            "toolCallName": "task",
        }
    )
    fold.apply(
        {
            "type": "SUBAGENT_STARTED",
            "subagentRunId": "child",
            "name": "nested",
            "description": "editor",
            "parentSubagentRunId": "parent",
            "parentToolCallId": "task",
        }
    )
    fold.apply(
        {
            "type": "SUBAGENT_FINISHED",
            "subagentRunId": "child",
            "outcome": {"type": "suspended", "interruptIds": ["actual"]},
            "result": {"sensitive": "do-not-copy"},
        }
    )
    fold.apply({"type": "RUN_FINISHED", "outcome": {"type": "interrupt"}})
    parent, child = fold.subagents()
    assert parent["status"] == "interrupted"
    assert child == {
        "subagentRunId": "child",
        "parentSubagentRunId": "parent",
        "parentToolCallId": "task",
        "parentMessageId": "p",
        "name": "nested",
        "description": "editor",
        "status": "interrupted",
        "interruptIds": ["actual"],
    }


def test_activity_replacement_keeps_same_lane_compaction_attachment() -> None:
    fold = AgUiRunSnapshot(public_activity_types={"moldy.compaction"})
    card = {"type": "ACTIVITY_SNAPSHOT", "activityType": "moldy.compaction", "messageId": "card"}
    fold.apply({**card, "content": {"state": "running"}}, source_ordinal=2)
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "assistant"}, source_ordinal=4)
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "other", "subagentRunId": "other-child"})
    fold.apply({**card, "replace": False, "content": {"state": "discarded"}})
    assert fold.messages()[0]["content"] == {"state": "running"}
    fold.apply({**card, "content": {"state": "done", "history_id": "history"}}, source_ordinal=6)
    assert fold.messages()[0]["content"] == {"state": "done", "history_id": "history"}
    assert fold.links_by_id()["card"] == {"parentMessageId": "assistant", "sourceOrdinal": 6}


def test_widget_requires_proven_lane_run_and_binding_and_omits_resource() -> None:
    run = "932968e9-1461-4a26-a1ba-826b39667c98"
    binding = "0d2f07df-efc0-40ae-8454-aaa9aa42c84a"
    fold = AgUiRunSnapshot(public_activity_types={"moldy.mcp_app"}, physical_run_id=run)
    content = {
        "run_id": run,
        "binding_id": binding,
        "tool_call_id": "raw-call",
        "public_tool_call_id": "call",
    }
    event = {"type": "ACTIVITY_SNAPSHOT", "activityType": "moldy.mcp_app", "messageId": "widget"}
    fold.apply({**event, "content": content})
    assert fold.messages() == []
    fold.apply(
        {
            "type": "TOOL_CALL_START",
            "parentMessageId": "host",
            "toolCallId": "call",
            "toolCallName": "fetch",
        }
    )
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "last"})
    fold.apply({**event, "content": {"run_id": run, "binding_id": binding, "tool_call_id": "call"}})
    fold.apply({**event, "content": {**content, "public_message_id": "last"}})
    fold.apply({**event, "content": {**content, "public_tool_call_id": "missing"}})
    fold.apply({**event, "subagentRunId": "other", "content": content})
    fold.apply({**event, "content": {**content, "run_id": "another-run"}})
    fold.apply({**event, "content": {**content, "binding_id": "malformed"}})
    assert "widget" not in fold.links_by_id()
    fold.apply({**event, "content": {**content, "resource_uri": "private"}})
    assert fold.messages()[-1]["content"] == content
    assert fold.links_by_id()["widget"] == {"parentMessageId": "host", "parentToolCallId": "call"}
    fold.apply(
        {
            "type": "TOOL_CALL_START",
            "parentMessageId": "last",
            "toolCallId": "call",
            "toolCallName": "fetch",
        }
    )
    fold.apply({**event, "messageId": "ambiguous", "content": content})
    assert "ambiguous" not in fold.links_by_id()


def test_unknown_frames_and_graph_state_never_become_user_history() -> None:
    fold = AgUiRunSnapshot()
    fold.apply({"type": "RAW", "rawEvent": "private"})
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "system", "role": "system"})
    fold.apply({"type": "TEXT_MESSAGE_CONTENT", "messageId": "system", "delta": "private"})
    fold.apply({"type": "REASONING_MESSAGE_CONTENT", "messageId": "thinking", "delta": "private"})
    fold.apply(
        {
            "type": "ACTIVITY_SNAPSHOT",
            "activityType": "arbitrary",
            "messageId": "private",
            "content": {"raw": "private"},
        }
    )
    fold.apply(
        {
            "type": "STATE_SNAPSHOT",
            "snapshot": {
                "credential": "private",
                "todos": [
                    {"content": "task", "status": "pending", "config": "private"},
                    {"invalid": 1},
                ],
            },
        }
    )
    fold.apply(
        {
            "type": "STATE_SNAPSHOT",
            "subagentRunId": "child",
            "snapshot": {
                "todos": [
                    {"id": "subtask", "content": "child task", "status": "completed"},
                ]
            },
        }
    )
    assert fold.messages() == []
    assert fold.todos_by_owner() == {
        None: [{"content": "task", "status": "pending"}],
        "child": [{"id": "subtask", "content": "child task", "status": "completed"}],
    }
    fold.apply({"type": "STATE_SNAPSHOT", "snapshot": {"todos": []}})
    assert fold.todos_by_owner()[None] == []


def test_failure_or_cancel_preserves_children_already_finished() -> None:
    for terminal, expected in [
        ({"type": "RUN_FINISHED", "outcome": {"type": "cancelled"}}, "cancelled"),
        ({"type": "RUN_ERROR", "message": "public"}, "failed"),
    ]:
        fold = AgUiRunSnapshot()
        for cid in ("safe", "done", "unfinished"):
            fold.apply({"type": "SUBAGENT_STARTED", "subagentRunId": cid, "name": "worker"})
        fold.apply(
            {
                "type": "SUBAGENT_ERROR",
                "subagentRunId": "safe",
                "message": "failed safely",
                "rawEvent": {"debug": "private"},
            }
        )
        fold.apply(
            {
                "type": "SUBAGENT_FINISHED",
                "subagentRunId": "done",
                "result": {"debug": "private"},
            }
        )
        fold.apply(terminal)
        assert fold.subagents() == [
            {
                "subagentRunId": "safe",
                "name": "worker",
                "status": "failed",
                "error": "failed safely",
            },
            {"subagentRunId": "done", "name": "worker", "status": "completed"},
            {"subagentRunId": "unfinished", "name": "worker", "status": expected},
        ]


def test_namespaced_widget_and_child_todos_use_public_owner_and_protected_lookup() -> None:
    run = "6fd19b16-94ff-4a19-be41-835e3c6810dc"
    fold = AgUiRunSnapshot(
        public_activity_types={"moldy.mcp_app", "moldy.subagent_todos"},
        physical_run_id=run,
    )
    fold.apply(
        {
            "type": "TOOL_CALL_START",
            "parentMessageId": "issuer",
            "toolCallId": "wire-call",
            "toolCallName": "render",
            "subagentRunId": "child",
        }
    )
    content = {
        "binding_id": "0fd802a9-e509-4281-9093-5906fd16d128",
        "tool_call_id": "raw",
        "run_id": run,
        "public_tool_call_id": "wire-call",
        "public_message_id": "issuer",
    }
    fold.apply(
        {
            "type": "ACTIVITY_SNAPSHOT",
            "messageId": "widget",
            "subagentRunId": "child",
            "activityType": "moldy.mcp_app",
            "content": content,
        }
    )
    assert fold.messages()[-1]["content"] == content
    assert fold.links_by_id()["widget"] == {
        "parentMessageId": "issuer",
        "parentToolCallId": "wire-call",
        "subagentRunId": "child",
    }
    todos = [{"id": "nested", "content": "finish", "status": "pending"}]
    fold.apply(
        {
            "type": "ACTIVITY_SNAPSHOT",
            "messageId": "todos",
            "activityType": "moldy.subagent_todos",
            "subagentRunId": "child",
            "content": {"todos": todos},
        }
    )
    assert fold.todos_by_owner() == {"child": todos}
    assert fold.messages()[-1]["content"] == {"todos": todos}


def test_ui_card_without_text_start_resolves_only_unique_issuing_call() -> None:
    fold = AgUiRunSnapshot(public_activity_types={"moldy.ui_data"})
    card = {
        "type": "ACTIVITY_SNAPSHOT",
        "activityType": "moldy.ui_data",
    }
    for lane, owner in (("main", {}), ("nested", {"subagentRunId": "child"})):
        issuer, call_id, mid = f"assistant-{lane}", f"call-{lane}", f"card-{lane}"
        fold.apply(
            {
                "type": "TOOL_CALL_START",
                "parentMessageId": issuer,
                "toolCallId": call_id,
                "toolCallName": "render",
                **owner,
            }
        )
        content = {"type": "table", "tool_call_id": call_id, "public_tool_call_id": call_id}
        fold.apply({**card, **owner, "messageId": mid, "content": content})
        assert fold.links_by_id()[mid] == {
            "parentMessageId": issuer,
            "parentToolCallId": call_id,
            **owner,
        }
    root_target = {
        "type": "table",
        "tool_call_id": "call-main",
        "public_tool_call_id": "call-main",
        "public_message_id": "assistant-main",
    }
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "other-root"})
    for claims in (
        {"type": "table", "tool_call_id": "call-main", "public_message_id": "assistant-main"},
        {**root_target, "public_message_id": "other-root"},
        {**root_target, "public_tool_call_id": "missing"},
        {**root_target, "tool_call_id": "raw-alias"},
        {**root_target, "tool_call_id": "call-nested", "public_tool_call_id": "call-nested"},
    ):
        fold.apply({**card, "messageId": "unproven", "content": claims})
        assert "unproven" not in fold.links_by_id()
    fold.apply({**card, "messageId": "card-nested", "replace": True, "content": root_target})
    assert fold.links_by_id()["card-nested"]["parentToolCallId"] == "call-nested"


def test_ui_card_without_a_tool_preserves_only_explicit_proven_message_parent() -> None:
    fold = AgUiRunSnapshot(public_activity_types={"moldy.ui_data"})
    fold.apply({"type": "TEXT_MESSAGE_START", "messageId": "visible"})
    card = {"type": "ACTIVITY_SNAPSHOT", "activityType": "moldy.ui_data"}
    for target in ({"message_id": "visible"}, {"public_message_id": "missing"}):
        fold.apply({**card, "messageId": "invalid", "content": {"type": "table", **target}})
        assert "invalid" not in fold.links_by_id()
    fold.apply({**card, "messageId": "standalone", "content": {"type": "table"}})
    assert fold.links_by_id()["standalone"] == {}
    fold.apply(
        {
            **card,
            "messageId": "attached",
            "content": {"type": "table", "message_id": "visible", "public_message_id": "visible"},
        }
    )
    assert fold.links_by_id()["attached"] == {"parentMessageId": "visible"}


def test_canonical_cancel_and_owned_interrupts_are_distinct() -> None:
    fold = AgUiRunSnapshot()
    for cid in ("cancelled", "waiting"):
        fold.apply({"type": "SUBAGENT_STARTED", "subagentRunId": cid, "name": "worker"})
    fold.apply(
        {
            "type": "SUBAGENT_ERROR",
            "subagentRunId": "cancelled",
            "code": "CANCELLED",
            "message": "Subagent cancelled.",
        }
    )
    fold.apply(
        {
            "type": "RUN_FINISHED",
            "outcome": {
                "type": "interrupt",
                "interrupts": [
                    {"id": "nested", "subagentRunId": "waiting"},
                    {"id": "root"},
                ],
            },
        }
    )
    cancelled, waiting = fold.subagents()
    assert cancelled["status"] == "cancelled"
    assert waiting["interruptIds"] == ["nested"]


@pytest.mark.parametrize("beta_completed", [False, True])
def test_targeted_interrupt_only_suspends_its_own_subtree_lineage(beta_completed: bool) -> None:
    fold = AgUiRunSnapshot()
    for owner, parent in (("parent", None), ("alpha", "parent"), ("beta", "parent"), ("old", None)):
        frame = {"type": "SUBAGENT_STARTED", "subagentRunId": owner, "name": "researcher"}
        if parent:
            frame["parentSubagentRunId"] = parent
        fold.apply(frame)
    if beta_completed:
        fold.apply({"type": "SUBAGENT_FINISHED", "subagentRunId": "beta", "result": "beta result"})
    fold.apply({"type": "SUBAGENT_FINISHED", "subagentRunId": "old", "result": "complete"})
    fold.apply(
        {
            "type": "RUN_FINISHED",
            "outcome": {
                "type": "interrupt",
                "interrupts": [
                    {"id": "root-approval"},
                    {"id": "approval", "subagentRunId": "alpha"},
                ],
            },
        }
    )
    by_owner = {child["subagentRunId"]: child for child in fold.subagents()}
    assert {key: child["status"] for key, child in by_owner.items()} == {
        "parent": "interrupted",
        "alpha": "interrupted",
        "beta": "completed" if beta_completed else "running",
        "old": "completed",
    }
    assert by_owner["alpha"]["interruptIds"] == ["approval"]
    assert all("interruptIds" not in by_owner[key] for key in ("parent", "beta", "old"))
