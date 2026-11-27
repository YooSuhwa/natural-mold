"""Public completion and interrupt contracts for checkpoint replay and live chat."""

import json
from typing import Any

import pytest
from ag_ui.core import RunErrorEvent, RunFinishedEvent
from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage

from app.agent_runtime.ag_ui_v1_outcomes import (
    PendingInterrupts,
    public_interrupt,
    run_terminal_event,
)
from app.agent_runtime.langgraph_protocol_adapter import adapt_stream_mode_chunk
from app.agent_runtime.protocol_events import ProtocolInterrupt, stored_protocol_event
from app.agent_runtime.runtime.connection_guard_policy import CredentialState
from tests.authz.f07_support import (
    ME,
    Ledger,
    calls,
    guard,
    guarded,
    plain_tool,
    respond,
    run_graph,
)


def _interrupt(
    wire_id: str, value: dict[str, Any], namespace: str | None = None
) -> ProtocolInterrupt:
    return {"id": wire_id, "ns": [namespace] if namespace else [], "value": value}


def _auth(attempt: int, group: str) -> dict[str, Any]:
    return {"type": "auth_required", "attempt": attempt, "auth_request_id": group, "items": []}


@pytest.mark.asyncio
async def test_real_connection_retry_exposes_only_the_answerable_id_and_attempt() -> None:
    ledger = Ledger(flip_after=3)
    ledger.states["c1"] = CredentialState("expired", ME, "mcp_oauth2")
    graph = run_graph(
        [calls("read"), AIMessage(content="done")],
        [plain_tool(ledger, "read")],
        [guard(ledger, guarded={"read": guarded("read", "c1")})],
    )
    first = await graph.start()
    (original,) = first["__interrupt__"]
    second = await graph.resume({original.id: respond("retry")})
    (current,) = second["__interrupt__"]
    assert original.id != current.id

    pending = PendingInterrupts()
    for seq, step in enumerate([first, second, first], start=1):
        # The last delivery is a stale replay received after the new state.
        saved = adapt_stream_mode_chunk(
            ("updates", {"__interrupt__": step["__interrupt__"]}),
            run_id="run",
            thread_id="thread",
            seq=seq,
        )
        pending.observe(saved, subagent_run_id=None)
    finished = run_terminal_event("thread", "run", "interrupted", pending)
    assert isinstance(finished, RunFinishedEvent)
    outcome = finished.model_dump(mode="json", by_alias=True, exclude_none=True)["outcome"]
    assert outcome["type"] == "interrupt"
    (item,) = outcome["interrupts"]
    assert item["id"] == current.id
    assert item["reason"] == "moldy.auth_required"
    assert item["metadata"]["moldy"]["auth_request_id"] == original.id
    assert item["metadata"]["moldy"]["attempt"] == 2
    validator = Draft202012Validator(item["responseSchema"])
    answer = {"decisions": [{"type": "respond", "message": "retry", "attempt": 2}]}
    assert validator.is_valid(answer)
    assert not validator.is_valid(
        {"decisions": [{"type": "respond", "message": "retry", "attempt": 1}]}
    )
    assert not validator.is_valid({"decisions": [{"type": "approve"}]})
    assert ledger.ran == []
    await graph.resume({item["id"]: answer})
    assert ledger.ran == ["read"]


def test_checkpoint_replaces_seen_interrupts_without_merging_sibling_owners() -> None:
    pending = PendingInterrupts()
    for owner, wire_id in [("left", "a"), ("right", "b"), ("right", "obsolete")]:
        value = _auth(1, "same-group") if wire_id != "obsolete" else {"type": "workflow_wait"}
        event = stored_protocol_event(
            run_id="run",
            thread_id="thread",
            seq=1,
            method="input.requested",
            namespace=[owner],
            data={"interrupt_id": wire_id, "payload": value},
        )
        pending.observe(event, subagent_run_id=owner)

    # The checkpointer's actual task input may no longer carry the child namespace.
    def identity(ns: tuple[str, ...]) -> str | None:
        return ns[0] if ns else None

    pending.replace_current(
        [_interrupt("a", _auth(1, "same-group")), _interrupt("b", _auth(1, "same-group"))],
        resolve_owner=identity,
        expires_at={"a": "2026-11-28T12:00:00Z"},
    )
    assert pending.for_owner("left") == ["a"]
    assert pending.for_owner("right") == ["b"]
    rows = {i.id: i for i in pending.public()}
    assert set(rows) == {"a", "b"}
    assert rows["a"].expires_at == "2026-11-28T12:00:00Z"
    assert rows["b"].expires_at is None
    assert rows["a"].subagent_run_id == "left"
    assert rows["b"].subagent_run_id == "right"
    pending.replace_current([], resolve_owner=identity)
    error = run_terminal_event("thread", "run", "interrupted", pending)
    assert isinstance(error, RunErrorEvent)
    assert error.code == "pending_input_unavailable"


def test_approval_schema_matches_decisions_to_each_tool_and_session_permission() -> None:
    value = {
        "action_requests": [
            {"name": "send", "args": {"subject": "분석"}, "extra": "hidden-action"},
            {"name": "read", "args": {"q": "기록"}},
        ],
        "review_configs": [
            {"action_name": "send", "allowed_decisions": ["edit", "reject"]},
            {
                "action_name": "read",
                "allowed_decisions": ["approve"],
                "session_consent_eligible": True,
            },
        ],
        "checkpoint_debug": "hidden-state",
    }
    result = public_interrupt(_interrupt("id", value, "hidden-ns"))
    assert result.reason == "moldy.tool_approval"
    assert result.message
    assert result.response_schema
    Draft202012Validator.check_schema(result.response_schema)
    validator = Draft202012Validator(result.response_schema)
    edit = {"type": "edit", "edited_action": {"name": "send", "args": {"subject": "수정"}}}
    reject = {"type": "reject", "message": "중단"}
    approve = {"type": "approve", "scope": "session"}
    assert validator.is_valid({"decisions": [edit, approve]})
    assert validator.is_valid({"decisions": [reject, approve]})
    for invalid in [
        [approve, reject],
        [reject],
        [reject, approve, approve],
        [{"type": "edit"}, approve],
    ]:
        assert not validator.is_valid({"decisions": invalid})
    assert "hidden-" not in result.model_dump_json(by_alias=True, exclude_none=True)


def test_ambiguous_child_wire_id_requires_an_authoritative_checkpoint() -> None:
    pending = PendingInterrupts()
    value = {"type": "workflow_wait"}
    # One resume id cannot distinguish which of the two checkpoint tasks it targets.
    slots = [_interrupt("same", value, owner) for owner in ("child-left", "child-right")]
    pending.replace_current(slots, resolve_owner=lambda ns: ns[0])
    assert pending.public() == []
    assert pending.for_owner("child-left") == []
    terminal = run_terminal_event("thread", "run", "interrupted", pending)
    assert isinstance(terminal, RunErrorEvent) and terminal.code == "pending_input_unavailable"
    pending.replace_current(slots[1:], resolve_owner=lambda ns: ns[0])
    assert pending.for_owner("child-right") == ["same"]


def test_native_question_preserves_choices_for_standard_and_moldy_clients() -> None:
    value = {
        "type": "ask_user",
        "mode": "option_list",
        "title": "확인할 파일",
        "options": [{"id": "report", "label": "보고서", "trace": "hidden-option"}],
        "minSelections": 1,
        "maxSelections": 1,
        "debug": "hidden-state",
    }
    result = public_interrupt(_interrupt("request", value))
    assert result.reason == "moldy.ask_user" and result.message
    assert result.metadata and result.response_schema
    assert result.metadata["moldy"]["options"] == [{"id": "report", "label": "보고서"}]
    schema = Draft202012Validator(result.response_schema)
    assert schema.is_valid(respond("보고서"))
    assert not schema.is_valid({"decisions": [{"type": "approve"}]})
    assert "hidden-" not in result.model_dump_json(by_alias=True, exclude_none=True)


def test_reconnect_card_lists_owned_connection_without_revealing_other_owners_credentials() -> None:
    value = _auth(1, "request")
    value["items"] = [
        {
            "resource_name": "Documents",
            "resource_type": "mcp_server",
            "fixable_by": owner,
            "credential_id": credential,
            "tool_names": ["read"],
            "connect": {"kind": "oauth", "headers": {"x-test": "hidden-header"}},
            "trace": "hidden-trace",
        }
        for owner, credential in [("me", "my-credential"), ("owner", "hidden-credential")]
    ]
    result = public_interrupt(_interrupt("id", value))
    assert result.metadata
    public = result.metadata["moldy"]["items"]
    assert public[0]["credential_id"] == "my-credential"
    assert public[0]["connect"] == {"kind": "oauth"}
    assert "hidden-" not in json.dumps(public)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ({"type": "workflow_wait", "workflow_run_id": "hidden-run"}, "moldy.workflow_wait"),
        ({"arbitrary": {"secret": "hidden-state"}}, "moldy.input_required"),
        (
            {
                "action_requests": [{"name": "send", "args": {"note": "hidden-args"}}],
                "review_configs": [{"action_name": "read", "allowed_decisions": ["approve"]}],
            },
            "moldy.input_required",
        ),
    ],
)
def test_passive_interrupt_does_not_publish_internal_payload_or_advertise_a_decision(
    value: dict[str, Any], reason: str
) -> None:
    item = public_interrupt(_interrupt("pending", value))
    wire = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire["id"] == "pending"
    assert wire["reason"] == reason
    assert "responseSchema" not in wire
    assert "hidden-" not in json.dumps(wire)


@pytest.mark.parametrize(
    ("status", "expected"), [("completed", "success"), ("canceled", "cancelled")]
)
def test_clean_run_terminal_outcome_contains_only_the_reported_state(
    status: str, expected: str
) -> None:
    event = run_terminal_event("thread", "run", status, PendingInterrupts())
    assert isinstance(event, RunFinishedEvent)
    wire = event.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire["runId"] == "run" and wire["threadId"] == "thread"
    assert wire["outcome"] == {"type": expected}


@pytest.mark.parametrize("code", ["partial_model_stream", "recovery_effect_unconfirmed"])
def test_terminal_error_preserves_public_failure_classification(code: str) -> None:
    error = run_terminal_event("thread", "run", "failed", PendingInterrupts(), error_code=code)
    assert isinstance(error, RunErrorEvent)
    assert error.code == code and error.message


def test_unknown_internal_failure_never_leaks_provider_error_in_code_or_message() -> None:
    error = run_terminal_event(
        "thread", "run", "failed", PendingInterrupts(), error_code="hidden-error"
    )
    assert isinstance(error, RunErrorEvent)
    assert error.code == "run_failed"
    assert "hidden-error" not in error.model_dump_json(by_alias=True, exclude_none=True)


def test_partial_or_passive_group_cannot_offer_a_legacy_child_resume() -> None:
    slots = [
        ProtocolInterrupt(id="child", ns=["nested"], value=_auth(1, "child")),
        ProtocolInterrupt(id="unhandled", ns=[], value={"type": "workflow_wait"}),
    ]
    pending = PendingInterrupts()
    pending.replace_current(
        slots,
        resolve_owner=lambda ns: "child-owner" if ns else None,
        legacy_child_ids={"child"},
        expires_at={"child": "2026-11-28T12:00:00Z"},
    )
    assert all(item.response_schema is None for item in pending.public())
    for item in pending.public():
        assert item.metadata and item.metadata["moldy"]["actionable"] is False
        assert "legacyInterruptAccess" not in item.metadata["moldy"]
    assert pending.public()[0].expires_at == "2026-11-28T12:00:00Z"
