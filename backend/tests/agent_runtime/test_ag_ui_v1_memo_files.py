"""The historical shared agent memo is prompt-only on the public chat wire."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pytest
from ag_ui.core import BaseEvent
from deepagents.backends.utils import format_grep_matches

from app.agent_runtime.ag_ui_v1_calls import PublicToolCalls
from app.agent_runtime.ag_ui_v1_interrupt_payload import interrupt_fields
from app.agent_runtime.ag_ui_v1_memo_files import private_memo_file_call
from app.agent_runtime.protocol_redaction import REDACTED_MEMORY_FIELD
from app.agent_runtime.runtime_filesystem_secure_search import secure_grep

MEMO = "/agents/00000000-0000-4000-8000-000000000001/AGENTS.md"
OUTPUT = "/conversations/thread-a/AGENTS.md"
PRIVATE = "prior governed memo body"
PUBLIC = "legitimate conversation content"


def args(name: str, path: str, body: str) -> dict[str, Any]:
    fields = {"file_path": path}
    if name == "write_file":
        fields["content"] = body
    if name == "edit_file":
        fields.update(old_string=body, new_string=body)
    return fields


def write_wire(frames: Sequence[BaseEvent]) -> str:
    return json.dumps([event.model_dump(mode="json", by_alias=True) for event in frames])


def proposal(calls: PublicToolCalls, name: str, params: dict[str, Any]) -> list[BaseEvent]:
    return calls.message(
        {"type": "ai", "tool_calls": [{"id": "call", "name": name, "args": params}]},
        parent_id="issuer",
        owner=None,
        event_id="assistant",
    )


def result(calls: PublicToolCalls, name: str, content: str) -> list[BaseEvent]:
    return calls.message(
        {"type": "tool", "tool_call_id": "call", "name": name, "content": content},
        parent_id="",
        owner=None,
        event_id="result",
    )


@pytest.mark.parametrize("name", ["read_file", "write_file", "edit_file"])
@pytest.mark.parametrize(("path", "body"), [(MEMO, PRIVATE), (OUTPUT, PUBLIC)])
def test_historical_file_contents_and_edits_follow_the_issued_path(
    name: str, path: str, body: str
) -> None:
    calls = PublicToolCalls("run", lambda *_: True)
    sent = proposal(calls, name, args(name, path, body))
    frames = sent + result(calls, name, body)
    assert any(event.type == "TOOL_CALL_RESULT" for event in frames)
    wire = write_wire(frames)
    if path == MEMO:
        assert PRIVATE not in wire and MEMO not in wire
        assert REDACTED_MEMORY_FIELD in wire
    else:
        assert PUBLIC in wire and OUTPUT in wire


@pytest.mark.parametrize(
    "path", [None, "/agents/../agents/id/AGENTS.md", "AGENTS.md", "/agents/id/agents.md"]
)
def test_unattributable_archived_file_results_withhold_the_body(path: str | None) -> None:
    calls = PublicToolCalls("run", lambda *_: True)
    sent = calls.tool(
        {
            "event": "tool-started",
            "tool_call_id": "call",
            "name": "read_file",
            "input": {"file_path": path},
        },
        owner=None,
        event_id="issued",
    )
    deltas = calls.tool(
        {"event": "tool-output-delta", "tool_call_id": "call", "delta": PRIVATE},
        owner=None,
        event_id="delta",
    )
    final = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call"}, owner=None, event_id="done"
    )
    wire = write_wire(sent + deltas + final)
    assert PRIVATE not in wire and REDACTED_MEMORY_FIELD in wire


def test_approved_execution_path_replaces_original_file_result_visibility() -> None:
    calls = PublicToolCalls("run", lambda *_: True)
    sent = proposal(calls, "read_file", args("read_file", OUTPUT, PUBLIC))
    sent += calls.tool(
        {
            "event": "tool-started",
            "tool_call_id": "call",
            "tool_name": "read_file",
            "input": {"file_path": MEMO},
        },
        owner=None,
        event_id="execution",
    )
    assert PRIVATE not in write_wire(sent + result(calls, "read_file", PRIVATE))


def test_protected_edit_approval_masks_bodies_descriptions_and_available_decisions() -> None:
    actions = [
        {"name": "edit_file", "args": args("edit_file", MEMO, PRIVATE), "description": PRIVATE},
        {"name": "edit_file", "args": args("edit_file", OUTPUT, PUBLIC), "description": PUBLIC},
    ]
    result = interrupt_fields(
        {
            "action_requests": actions,
            "review_configs": [
                {"action_name": "edit_file", "allowed_decisions": ["approve", "edit", "reject"]}
                for _ in actions
            ],
        }
    )
    assert PRIVATE not in json.dumps(result)
    assert MEMO not in json.dumps(result)
    assert result["reason"] == "moldy.tool_approval"
    cards = result["metadata"]["moldy"]
    assert cards["action_requests"][1] == actions[1]
    assert cards["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert "edit" in cards["review_configs"][1]["allowed_decisions"]
    schema = result["response_schema"]["properties"]["decisions"]["prefixItems"]
    assert "edited_action" not in json.dumps(schema[0])
    assert "edited_action" in json.dumps(schema[1])


def test_conflicting_historical_file_paths_do_not_establish_a_public_body() -> None:
    assert private_memo_file_call("read_file", {"file_path": OUTPUT, "path": MEMO})
    assert not private_memo_file_call("get_event", {"file_path": MEMO})


@pytest.mark.parametrize("path", [MEMO, MEMO.rsplit("/", 1)[0], "/agents"])
def test_explicit_memo_grep_keeps_the_pattern_and_matched_lines_private(path: str) -> None:
    params = {"path": path, "pattern": PRIVATE, "output_mode": "content"}
    calls = PublicToolCalls("run", lambda *_: True)
    emitted = proposal(calls, "grep", params) + result(calls, "grep", PRIVATE)
    assert PRIVATE not in write_wire(emitted)
    assert REDACTED_MEMORY_FIELD in write_wire(emitted)
    shown = interrupt_fields(
        {
            "action_requests": [{"name": "grep", "args": params, "description": PRIVATE}],
            "review_configs": [
                {"action_name": "grep", "allowed_decisions": ["approve", "reject", "edit"]}
            ],
        }
    )
    assert PRIVATE not in json.dumps(shown)
    assert shown["metadata"]["moldy"]["action_requests"][0]["args"] == {
        "path": REDACTED_MEMORY_FIELD
    }


@pytest.mark.parametrize("mode", ["files_with_matches", "count", "content"])
def test_mixed_grep_keeps_only_ordinary_file_groups_from_native_formatter(
    tmp_path: Path, mode: Literal["files_with_matches", "count", "content"]
) -> None:
    for path, body in [(MEMO, PRIVATE), (OUTPUT, PUBLIC)]:
        file = tmp_path / path.lstrip("/")
        file.parent.mkdir(parents=True)
        file.write_text("found " + body + "\n", encoding="utf-8")
    hits = secure_grep(tmp_path, "/", (), "found", None, None, frozenset())
    matches = hits.matches or []
    assert {hit["path"] for hit in matches} == {MEMO, OUTPUT}
    formatted = format_grep_matches(matches, mode)
    expected = format_grep_matches([hit for hit in matches if hit["path"] == OUTPUT], mode)
    calls = PublicToolCalls("run", lambda *_: True)
    proposal(calls, "grep", {"pattern": "found", "output_mode": mode})
    [reply] = result(calls, "grep", formatted)
    assert reply.model_dump()["content"] == expected


def test_ordinary_grep_preserves_matched_lines_that_mention_the_memo() -> None:
    content = OUTPUT + ":\n  1: A reference to " + MEMO
    calls = PublicToolCalls("run", lambda *_: True)
    proposal(calls, "grep", {"path": OUTPUT, "pattern": "reference", "output_mode": "content"})
    [reply] = result(calls, "grep", content)
    assert reply.model_dump()["content"] == content
