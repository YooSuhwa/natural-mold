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
    activities = PublicActivities(readable_widget_bindings=frozenset({("call/1:widget", BINDING)}))
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
    for unauthorized in (
        PublicActivities(),
        PublicActivities(readable_widget_bindings=None),
        PublicActivities(readable_widget_bindings=frozenset({("child-tool-1", BINDING)})),
        PublicActivities(readable_widget_bindings=frozenset({("call/1:widget", VERSION)})),
    ):
        assert _frames(unauthorized, result, parent="owner", tool="child-tool-1") == []
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
    from ag_ui.core import TextMessageStartEvent

    activities.observe_visible_messages(
        [TextMessageStartEvent(message_id="verified-model", role="assistant")]
    )
    assert _frames(activities, start, parent="verified-model") == []
    assert _frames(activities, finish, parent="verified-model") == []
    usage = _custom(
        "usage",
        {
            "run_id": PHYSICAL,
            "prompt_tokens": 17,
            "completion_tokens": 5,
            "tokens_per_second": 3.0,
            "provider_private": SECRET,
        },
        event_id="finish:usage",
    )
    [card] = _frames(activities, usage)
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
    assert first["content"]["run_id"] == PUBLIC
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


def test_native_tool_cards_require_issued_visible_calls_and_concordant_messages() -> None:
    from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility
    from app.agent_runtime.ui_data_projection import ui_data_from_tool_result

    stream = AgUiV1Projector(
        thread_id=THREAD,
        run_id=PUBLIC,
        source_run_id=PHYSICAL,
        child_visibility=ChildVisibility(False, frozenset(), frozenset()),
    )
    stream.project(
        _event(
            "messages",
            {
                "type": "ai",
                "id": "allowed-message",
                "content": "작업",
                "tool_calls": [{"name": "execute_in_skill", "id": "call/allowed:1", "args": {}}],
            },
        )
    )
    stream.project(
        _event(
            "messages",
            {
                "type": "ai",
                "id": "hidden-message",
                "tool_calls": [{"name": "task", "id": "revoked-task", "args": {}}],
            },
        )
    )
    stream.project(
        _event("messages", {"type": "ai", "id": "other-message", "content": "다른 메시지"})
    )
    [native] = ui_data_from_tool_result(
        "execute_in_skill", "허용된 출력", tool_call_id="call/allowed:1"
    )

    def project_card(data: dict[str, Any], suffix: str) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in stream.project(
                _custom("moldy.ui_data", data, event_id=f"result:ui_data:{suffix}")
            )
        ]

    [allowed] = project_card({**native, "message_id": "allowed-message"}, "allowed")
    assert allowed["content"]["props"] == {"lines": "허용된 출력"}
    assert allowed["content"]["public_tool_call_id"] == allowed["content"]["tool_call_id"]
    assert allowed["content"]["message_id"] == allowed["content"]["public_message_id"]
    for changes in (
        {"tool_call_id": "revoked-task"},
        {"tool_call_id": "absent"},
        {"tool_call_id": None, "message_id": "hidden-message"},
        {"tool_call_id": None, "message_id": "absent"},
        {"message_id": "other-message"},
        {"tool_call_id": None, "toolCallId": "revoked-task"},
        {"tool_call_id": None, "public_tool_call_id": "fake-proof"},
    ):
        assert project_card({**native, **changes, "props": {"lines": SECRET}}, "invalid") == []
    assert project_card({"type": "terminal", "props": {"lines": SECRET}}, "0") == []
    [independent] = project_card(
        {"type": "demo_note", "props": {"text": "독립 안내"}}, "independent"
    )
    assert independent["content"]["props"] == {"text": "독립 안내"}


def test_public_usage_requires_a_real_post_visibility_receipt_in_both_journal_formats() -> None:
    from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility
    from app.agent_runtime.protocol_usage import collect_protocol_usage_event

    stream = AgUiV1Projector(
        thread_id=THREAD,
        run_id=PUBLIC,
        source_run_id=PHYSICAL,
        child_visibility=ChildVisibility(False, frozenset(), frozenset()),
    )
    visible = _event(
        "messages",
        {
            "type": "ai",
            "id": "visible-model",
            "content": "답변",
            "usage_metadata": {"input_tokens": 9, "output_tokens": 3},
        },
        event_id="visible-source",
    )
    hidden = _event(
        "messages",
        {
            "type": "ai",
            "id": "hidden-model",
            "content": "hidden",
            "usage_metadata": {"input_tokens": 70, "output_tokens": 10},
        },
        event_id="hidden-source",
    )
    hidden["namespace"] = ["tools:revoked"]
    stream.project(visible)
    stream.project(hidden)

    def receipt(source: StoredProtocolEvent) -> StoredProtocolEvent:
        result, _ = collect_protocol_usage_event(
            source,
            next_seq=3,
            seen_keys=set(),
            usage_sink=None,
            cost_per_input_token=None,
            cost_per_output_token=None,
        )
        assert result is not None
        return result

    hidden_result = receipt(hidden)
    hidden_result["namespace"] = []  # forged root re-envelope after the source was denied
    assert stream.project(hidden_result) == []
    real = receipt(visible)
    payload = real["data"]["payload"]
    for forged in (
        _custom("usage", {**payload, "prompt_tokens": 999999}, event_id="invented:usage"),
        _custom("moldy.usage", payload, event_id=real["id"]),
        _custom("usage", {**payload, "assistant_msg_id": "foreign-model"}, event_id=real["id"]),
        _custom("usage", {**payload, "run_id": PUBLIC}, event_id=real["id"]),
    ):
        assert stream.project(forged) == []
    [card] = stream.project(real)
    assert card.model_dump(by_alias=True)["content"]["public_message_id"] == "visible-model"
    legacy = _event("custom:usage", payload, event_id=real["id"])
    assert stream.project(legacy) == []  # duplicate public snapshot
    # A fresh stream reading only the historical method gets the identical public card.
    restored = AgUiV1Projector(thread_id=THREAD, run_id=PUBLIC, source_run_id=PHYSICAL)
    restored.project(visible)
    assert restored.project(legacy) == [card]


@pytest.mark.parametrize(("name", "visible"), [("execute_in_skill", True), ("task", False)])
def test_only_a_visible_tool_can_surface_its_otherwise_textless_model_id(
    name: str, visible: bool
) -> None:
    from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility
    from app.agent_runtime.protocol_usage import collect_protocol_usage_event

    stream = AgUiV1Projector(
        thread_id=THREAD,
        run_id=PUBLIC,
        source_run_id=PHYSICAL,
        child_visibility=ChildVisibility(False, frozenset(), frozenset()),
    )
    issued = _event(
        "messages",
        {
            "id": "tool-only-model",
            "type": "ai",
            "content": "",
            "tool_calls": [{"id": "call/one", "name": name, "args": {}}],
            "usage_metadata": {"input_tokens": 5, "output_tokens": 2},
        },
        event_id="provider",
    )
    wire = stream.project(issued)
    receipt, _ = collect_protocol_usage_event(
        issued,
        next_seq=2,
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )
    assert receipt is not None
    cards = stream.project(receipt)
    assert bool(cards) is visible
    if visible:
        assert (
            cards[0].model_dump(by_alias=True)["content"]["public_message_id"] == "tool-only-model"
        )
    else:
        assert "tool-only-model" not in str(wire)
    assert (
        stream.finish("completed")[-1].model_dump(mode="json", by_alias=True)["usage"][0][
            "totalTokens"
        ]
        == 7
    )
