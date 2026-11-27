"""The durable chat stream contains public cards and guarded resource references."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime.ag_ui_v1_activities import PUBLIC_ACTIVITY_TYPES, PublicActivities
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)

THREAD = "10000000-0000-4000-8000-000000000000"
PHYSICAL = "20000000-0000-4000-8000-000000000000"
PUBLIC = "30000000-0000-4000-8000-000000000000"
ARTIFACT = "40000000-0000-4000-8000-000000000000"
VERSION = "50000000-0000-4000-8000-000000000000"
BINDING = "60000000-0000-4000-8000-000000000000"
STEER = "70000000-0000-4000-8000-000000000000"
SECRET = "DO_NOT_PUBLISH"
TODO = {"id": "todo-1", "content": "파일 확인", "status": "in_progress"}


def _event(method: str, data: object, *, event_id: str = "parent") -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=PHYSICAL, thread_id=THREAD, seq=1, event_id=event_id, method=method, data=data
    )


def _custom(name: str, payload: object, *, event_id: str = "card") -> StoredProtocolEvent:
    return stored_custom_protocol_event(
        run_id=PHYSICAL, thread_id=THREAD, seq=1, event_id=event_id, name=name, payload=payload
    )


def _frames(
    activities: PublicActivities,
    event: StoredProtocolEvent,
    *,
    child: str | None = None,
    parent: str | None = None,
    tool: str | None = None,
) -> list[dict[str, Any]]:
    return [
        frame.model_dump(mode="json", by_alias=True, exclude_none=True)
        for frame in activities.project(
            event,
            run_id=PUBLIC,
            subagent_run_id=child,
            public_message_id=parent,
            public_tool_call_id=tool,
        )
    ]


def test_todos_scope_and_replay_preserve_root_state() -> None:
    activities = PublicActivities()
    state = _event(
        "values",
        {
            "_checkpoint": SECRET,
            "files": {"/private": SECRET},
            "todos": [{**TODO, "diagnostics": SECRET}],
        },
    )
    assert _frames(activities, state) == [{"type": "STATE_SNAPSHOT", "snapshot": {"todos": [TODO]}}]
    assert _frames(activities, state) == []
    [child] = _frames(activities, state, child="approved-child")
    assert child["type"] == "ACTIVITY_SNAPSHOT" and child["activityType"] == "moldy.subagent_todos"
    assert child["content"] == {"todos": [TODO]} and child["subagentRunId"] == "approved-child"
    assert _frames(activities, state, child="approved-child") == []
    assert (
        _frames(
            activities, _event("updates", {"todos": [{"content": SECRET, "status": "unknown"}]})
        )
        == []
    )
    assert (
        _frames(activities, _event("values", {"tasks": [{"id": SECRET}], "checkpoint": SECRET}))
        == []
    )
    assert _frames(activities, _event("values", {"todos": []})) == [
        {"type": "STATE_SNAPSHOT", "snapshot": {"todos": []}}
    ]


def test_memory_cards_redact_contents_and_editor_memos() -> None:
    activities = PublicActivities()
    [card] = _frames(
        activities,
        _custom(
            "moldy.memory_recalled",
            {
                "memories": [
                    {
                        "id": ARTIFACT,
                        "scope": "agent",
                        "content": SECRET,
                        "reason": SECRET,
                        "path": "auto",
                    },
                    {"scope": "private", "content": SECRET},
                ]
            },
        ),
    )
    assert card["activityType"] == "moldy.memory_recalled" and card["content"] == {
        "memories": [{"id": ARTIFACT, "scope": "agent", "content": "<redacted>", "path": "auto"}]
    }
    [memo] = _frames(
        activities,
        _custom(
            "agent_memo_proposed",
            {
                "scope": "agent_memo",
                "id": VERSION,
                "content": SECRET,
                "visible_to": "agent_editors",
                "reason": SECRET,
            },
        ),
    )
    assert memo["content"] == {"scope": "agent_memo", "id": VERSION, "content": "<redacted>"}
    assert (
        _frames(activities, _custom("agent_memo_proposed", {"scope": "agent", "id": VERSION})) == []
    )


def test_compaction_updates_a_single_activity_without_disclosing_archives() -> None:
    activities = PublicActivities()
    [running] = _frames(
        activities, _custom("moldy.compaction", {"state": "running", "summary": SECRET})
    )
    assert running["content"] == {"state": "running"}
    history = "history_" + "123456abcdef" * 2
    done = _custom(
        "moldy.compaction",
        {
            "state": "done",
            "history_id": history,
            "cutoff_index": 4,
            "offload_path": "/server/private",
            "summary": SECRET,
        },
    )
    [complete] = _frames(activities, done)
    assert complete["messageId"] == running["messageId"]
    assert complete["content"] == {"state": "done", "history_id": history, "cutoff_index": 4}
    assert _frames(activities, done) == []
    [child] = _frames(activities, done, child="approved-child")
    assert child["messageId"] != complete["messageId"] and "subagentRunId" in child
    [bad] = _frames(
        activities,
        _custom(
            "moldy.compaction",
            {"state": "done", "history_id": "/private/file", "cutoff_index": True},
        ),
    )
    assert bad["content"] == {"state": "done"}


def test_file_card_requires_current_exact_grant_and_hides_all_journal_metadata() -> None:
    payload = {
        "id": ARTIFACT,
        "version_id": VERSION,
        "op": "updated",
        "conversation_id": THREAD,
        "run_id": PHYSICAL,
        "metadata": SECRET,
        "linked_message_ids": [SECRET],
        "description": SECRET,
        "path": "/server/private",
        "preview_url": "https://private.invalid/",
    }
    event = _custom("file_event", payload)
    assert _frames(PublicActivities(), event) == []
    activities = PublicActivities(readable_artifact_versions=frozenset({(ARTIFACT, VERSION)}))
    [card] = _frames(activities, event, parent="verified-owner")
    assert card["activityType"] == "moldy.file_event" and card["content"] == {
        "id": ARTIFACT,
        "version_id": VERSION,
        "op": "updated",
        "public_message_id": "verified-owner",
    }
    for invalid in (
        {"id": BINDING},
        {"version_id": BINDING},
        {"run_id": PUBLIC},
        {"conversation_id": PHYSICAL},
        {"op": SECRET},
    ):
        assert _frames(activities, _custom("file_event", {**payload, **invalid})) == []


def test_mcp_widgets_export_only_stable_binding_reference_across_journal_variants() -> None:
    activities = PublicActivities()
    app = {
        "version": 1,
        "binding_id": BINDING,
        "run_id": PHYSICAL,
        "resource_uri": "ui://hidden",
        "result_meta": {SECRET: 1},
    }
    data = {
        "tool_call_id": "call/1:widget",
        "artifact": {"mcp_app": app, "structured_content": {SECRET: 1}},
        "content": SECRET,
    }
    result = _event("messages", {**data, "type": "tool"})
    assert _frames(activities, result, parent="owner") == []
    [card] = _frames(activities, result, parent="owner", tool="child-tool-1")
    assert card["activityType"] == "moldy.mcp_app" and card["content"] == {
        "binding_id": BINDING,
        "run_id": PHYSICAL,
        "tool_call_id": "call/1:widget",
        "public_message_id": "owner",
        "public_tool_call_id": "child-tool-1",
    }
    assert (
        _frames(
            activities,
            _event("tools", {**data, "event": "tool-finished"}),
            parent="owner",
            tool="child-tool-1",
        )
        == []
    )
    for invalid in ({"run_id": PUBLIC}, {"binding_id": "local-secret"}, {"version": True}):
        changed = {**data, "type": "tool", "artifact": {"mcp_app": {**app, **invalid}}}
        assert _frames(activities, _event("messages", changed), tool="child-tool-1") == []
    assert _frames(activities, _custom("moldy.mcp_app", data)) == []


def test_steer_reserved_event_accepts_canonical_receipt_only() -> None:
    activities = PublicActivities()
    receipt = {
        "conversation_id": THREAD,
        "run_id": PHYSICAL,
        "steer_id": STEER,
        "status": "applied",
    }
    event_id = f"{STEER}:steer:applied"
    [card] = _frames(activities, _custom("moldy.run_steer", receipt, event_id=event_id))
    assert card["activityType"] == "moldy.run_steer" and card["content"] == receipt
    for invalid in ({"text": SECRET}, {"run_id": PUBLIC}, {"status": "queued"}):
        assert (
            _frames(
                activities, _custom("moldy.run_steer", {**receipt, **invalid}, event_id=event_id)
            )
            == []
        )
    assert _frames(activities, _custom("moldy.run_steer", receipt)) == []


def test_usage_bridge_binds_real_model_start_finish_and_ignores_exact_derived_duplicate() -> None:
    activities = PublicActivities()
    start = _event(
        "messages",
        {
            "event": "message-start",
            "role": "ai",
            "id": "source:1",
            "metadata": {"provider": "acme", "model": "test"},
        },
    )
    finish = _event(
        "messages",
        {
            "event": "message-finish",
            "usage": {"input_tokens": 17, "output_tokens": 5},
            "metadata": {"model_provider": "acme", "model_name": "test"},
        },
        event_id="finish",
    )
    assert _frames(activities, start, parent="verified-model") == []
    assert _frames(activities, finish, parent="verified-model") == []
    usage = _custom(
        "usage",
        {
            "prompt_tokens": 17,
            "completion_tokens": 5,
            "tokens_per_second": 3.0,
            "provider_private": SECRET,
        },
        event_id="finish:usage",
    )
    [card] = _frames(activities, usage, parent="verified-model")
    assert card["content"] == {
        "prompt_tokens": 17,
        "completion_tokens": 5,
        "tokens_per_second": 3.0,
        "run_id": PUBLIC,
        "public_message_id": "verified-model",
    }
    assert (
        _frames(
            activities,
            _custom("usage", {"prompt_tokens": 1, "completion_tokens": 2, "run_id": THREAD}),
        )
        == []
    )
    totals = activities.terminal_usage()
    assert totals is not None
    assert [(u.provider, u.model, u.total_tokens) for u in totals] == [("acme", "test", 22)]


@pytest.mark.parametrize(
    "name", ["moldy.unknown", "moldy.goal_private", "moldy.subagent_todos", "moldy.mcp_app"]
)
def test_unknown_or_generated_only_channels_are_inert(name: str) -> None:
    assert _frames(PublicActivities(), _custom(name, {"payload": SECRET})) == []
    assert "moldy.unknown" not in PUBLIC_ACTIVITY_TYPES


def test_ui_data_keeps_multiple_same_type_cards_emitted_by_one_tool() -> None:
    activities = PublicActivities()
    body = {
        "schema_version": 1,
        "type": "demo_note",
        "props": {"text": "첫째", "trace": SECRET},
        "run_id": PHYSICAL,
        "tool_call_id": "call-1",
    }
    [first] = _frames(
        activities, _custom("ui_data", body, event_id="source:ui_data:0"), tool="call-visible"
    )
    second = _custom("ui_data", {**body, "props": {"text": "둘째"}}, event_id="source:ui_data:1")
    [next_card] = _frames(activities, second, tool="call-visible")
    assert first["messageId"] != next_card["messageId"]
    assert first["content"]["public_tool_call_id"] == "call-visible"
    assert first["content"]["props"] == {"text": "첫째"}
    assert _frames(activities, second, tool="call-visible") == []


def test_exclusion_summary_rebuilds_counts_from_runtime_capability_kinds() -> None:
    activities = PublicActivities()
    items = [
        {"type": "mcp_tool", "reason": "broken", "count": 2, "resource_id": SECRET},
        {"type": SECRET, "reason": "denied", "count": 5},
        {"type": "tool", "reason": "denied", "count": -1},
    ]
    [card] = _frames(
        activities, _custom("moldy.excluded_capabilities", {"items": items, "total": 999})
    )
    assert card["content"] == {
        "items": [{"type": "mcp_tool", "reason": "broken", "count": 2}],
        "total": 2,
    }


def test_archived_message_metadata_preserves_observed_provider_totals() -> None:
    activities = PublicActivities()
    sample = [
        {"id": "old-id", "type": "ai", "usage_metadata": {"input_tokens": 3, "output_tokens": 4}},
        {"ls_provider": "acme", "ls_model_name": "old"},
    ]
    assert _frames(activities, _event("messages", sample), parent="bound-message") == []
    totals = activities.terminal_usage()
    assert totals and [(u.provider, u.model, u.total_tokens) for u in totals] == [
        ("acme", "old", 7)
    ]
