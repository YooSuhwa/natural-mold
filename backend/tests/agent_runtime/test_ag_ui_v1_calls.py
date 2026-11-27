"""Verify AG-UI tool messages from the persisted and streaming LangGraph event shapes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from ag_ui.core import BaseEvent

from app.agent_runtime.ag_ui_v1_calls import PublicToolCalls

_ARCHIVE = Path(__file__).with_name("fixtures") / "delegated_chat_0_7_32.jsonl"


def _wire(events: Sequence[BaseEvent]) -> list[dict[str, Any]]:
    return [event.model_dump(by_alias=True, mode="json", exclude_none=True) for event in events]


def _allow(_name: str | None, _call_id: str) -> bool:
    return True


def _proposal(call_id: str = "call", name: str = "search") -> dict[str, Any]:
    return {"type": "ai", "tool_calls": [{"id": call_id, "name": name, "args": {"q": "서울"}}]}


def test_recorded_delegation_is_one_call_and_one_result() -> None:
    calls = PublicToolCalls("root", _allow)
    events: list[BaseEvent] = []
    for line in _ARCHIVE.read_text().splitlines():
        row = json.loads(line)
        if row["method"] == "messages" and not row["namespace"]:
            events.extend(
                calls.message(
                    row["data"], owner=None, parent_id=row["data"]["id"], event_id=row["id"]
                )
            )
        elif row["method"] == "tools":
            events.extend(calls.tool(row["data"], owner=None, event_id=row["id"]))
    events.extend(calls.close(None))

    frames = _wire(events)
    assert [frame["type"] for frame in frames] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    start, args, _, result = frames
    assert start["parentMessageId"] == calls.parent_id(None, "call-research") == "boss-task"
    assert {frame["toolCallId"] for frame in frames} == {"call-research"}
    assert json.loads(args["delta"]) == {
        "description": "fixture only",
        "subagent_type": "researcher",
    }
    assert result["content"] == "대상은 세 곳입니다."
    assert result["messageId"] not in {"call-research", "boss-task", "boss-final"}
    assert "metadata" not in result


def test_native_tool_block_fragments_and_final_snapshot_emit_one_safe_argument() -> None:
    secret = "private-credential-probe"
    calls = PublicToolCalls("root", _allow, secret_values=(secret,))
    start = {
        "event": "content-block-start",
        "index": 0,
        "content": {"type": "tool_call_chunk", "id": "search-call", "name": "search", "args": ""},
    }
    parts = ['{"q":"private-cred', 'ential-probe","limit":0}']
    finish = {
        "event": "content-block-finish",
        "index": 0,
        "content": {
            "type": "tool_call",
            "id": "search-call",
            "name": "search",
            "args": {"q": secret, "limit": 0},
        },
    }
    rows = [
        start,
        *(
            {
                "event": "content-block-delta",
                "index": 0,
                "delta": {"type": "tool-call-delta", "args": part},
            }
            for part in parts
        ),
        finish,
    ]
    events: list[BaseEvent] = []
    for i, row in enumerate(rows):
        events.extend(calls.message(row, owner=None, parent_id="answer", event_id=str(i)))
    events.extend(
        calls.tool(
            {
                "event": "tool-started",
                "tool_call_id": "search-call",
                "tool_name": "search",
                "input": {"q": secret, "limit": 0},
            },
            owner=None,
            event_id="started",
        )
    )
    events.extend(calls.close(None))
    frames = _wire(events)
    serialized = json.dumps(frames, ensure_ascii=False)
    assert "private-cred" not in serialized and "ential-probe" not in serialized
    assert [frame["type"] for frame in frames] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert json.loads(frames[1]["delta"])["limit"] == 0
    assert calls.parent_id(None, "search-call") == "answer"


def test_interleaved_parallel_chunk_indices_keep_their_own_arguments() -> None:
    calls = PublicToolCalls("root", _allow)
    events: list[BaseEvent] = []
    for index, chunks in enumerate(
        [
            [
                {"id": "a", "name": "search", "index": 0, "args": '{"q":"'},
                {"id": "b", "name": "search", "index": 1, "args": '{"q":"'},
            ],
            [
                {"id": None, "name": None, "index": 1, "args": 'second"}'},
                {"id": None, "name": None, "index": 0, "args": 'first"}'},
            ],
        ]
    ):
        # AIMessageChunk supplies best-effort parsed tool_calls alongside raw chunks.
        data = {
            "type": "AIMessageChunk",
            "tool_call_chunks": chunks,
            "tool_calls": [{"id": "a", "name": "search", "args": {}}],
        }
        events.extend(calls.message(data, owner=None, parent_id="answer", event_id=str(index)))
    events.extend(calls.close(None))
    args_by_id = {
        frame["toolCallId"]: json.loads(frame["delta"])
        for frame in _wire(events)
        if frame["type"] == "TOOL_CALL_ARGS"
    }
    assert args_by_id == {"a": {"q": "first"}, "b": {"q": "second"}}


def test_owner_isolation_preserves_parent_mapping_for_colliding_provider_call_ids() -> None:
    calls = PublicToolCalls("run", _allow)
    events: list[BaseEvent] = []
    for owner, parent in [(None, "p"), ("child-a", "pa"), ("child-b", "pb")]:
        events.extend(calls.message(_proposal(), owner=owner, parent_id=parent, event_id=parent))
    for owner, value in [("child-b", "b"), (None, "root"), ("child-a", "a")]:
        events.extend(
            calls.tool(
                {"event": "tool-finished", "tool_call_id": "call", "output": value},
                owner=owner,
                event_id=value,
            )
        )
        events.extend(calls.close(owner))
    frames = _wire(events)
    ids = {owner: calls.public_id(owner, "call") for owner in (None, "child-a", "child-b")}
    assert ids[None] == "call"
    assert all(ids.values()) and len(set(ids.values())) == 3
    for owner, parent, value in [
        (None, "p", "root"),
        ("child-a", "pa", "a"),
        ("child-b", "pb", "b"),
    ]:
        own = [frame for frame in frames if frame["toolCallId"] == ids[owner]]
        assert [frame["type"] for frame in own] == [
            "TOOL_CALL_START",
            "TOOL_CALL_ARGS",
            "TOOL_CALL_END",
            "TOOL_CALL_RESULT",
        ]
        assert all(frame.get("subagentRunId") == owner for frame in own)
        assert own[0]["parentMessageId"] == calls.parent_id(owner, "call") == parent
        assert own[-1]["content"] == value


def test_denied_call_cannot_reappear_from_untyped_or_renamed_records() -> None:
    calls = PublicToolCalls("root", lambda name, _id: name != "task")
    frames = calls.message(_proposal(name="task"), owner=None, parent_id="p", event_id="1")
    for i, row in enumerate(
        [
            {"event": "tool-started", "tool_call_id": "call", "tool_name": "search", "input": {}},
            {"event": "tool-finished", "tool_call_id": "call", "output": "child only"},
            {"event": "tool-finished", "tool_call_id": "untracked", "output": "unknown"},
        ]
    ):
        frames.extend(calls.tool(row, owner=None, event_id=str(i + 2)))
    frames.extend(calls.close(None))
    assert frames == []
    assert calls.public_id(None, "call") is None
    assert calls.parent_id(None, "call") is None


def test_command_result_exposes_only_the_tool_message_answering_this_call() -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="1")
    output = {
        "graph": "private graph",
        "goto": ["private node"],
        "update": {
            "private_state": "private store",
            "messages": [
                {"type": "tool", "tool_call_id": "sibling", "content": "private sibling"},
                {
                    "type": "tool",
                    "tool_call_id": "call",
                    "content": [
                        {"type": "reasoning", "reasoning": "private reasoning"},
                        {"type": "image", "data": "private binary"},
                        {"type": "text", "text": "visible"},
                    ],
                    "response_metadata": {"private": "private trace"},
                },
            ],
        },
    }
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": output},
        owner=None,
        event_id="2",
    )
    events.extend(calls.close(None))
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert "visible" in json.dumps(result["content"])
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(("value", "expected"), [(False, "false"), (0, "0"), ("", "")])
def test_falsy_tool_results_survive(value: object, expected: str) -> None:
    calls = PublicToolCalls("root", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="1")
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": value},
        owner=None,
        event_id="2",
    )
    events.extend(calls.close(None))
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == expected


def test_error_is_owned_by_the_tool_and_marked_in_namespaced_metadata() -> None:
    calls = PublicToolCalls("root", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="1")
    error = {"event": "tool-error", "tool_call_id": "call", "message": "request failed"}
    events = calls.tool(error, owner=None, event_id="2")
    events.extend(calls.tool(error, owner=None, event_id="2"))
    events.extend(calls.close(None))
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["toolCallId"] == "call"
    assert result["metadata"] == {"moldy": {"isError": True}}
    assert "isError" not in result


def test_revocation_after_issuance_prevents_private_result_disclosure() -> None:
    allowed_calls = {"call"}
    calls = PublicToolCalls("run", lambda _name, call_id: call_id in allowed_calls)
    calls.message(_proposal(name="task"), owner=None, parent_id="parent", event_id="1")

    allowed_calls.clear()
    withheld = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "content": "revoked private result"},
        owner=None,
        event_id="2",
    )
    withheld.extend(calls.close(None))
    assert "revoked private result" not in json.dumps(_wire(withheld))
    assert not any(frame["type"] == "TOOL_CALL_RESULT" for frame in _wire(withheld))


@pytest.mark.parametrize("complete", [True, False])
def test_fragmented_sensitive_field_is_masked_even_without_a_run_secret(complete: bool) -> None:
    calls = PublicToolCalls("run", _allow)
    pieces = ['{"public":"ok","creds":{"api', 'Key":"hidden', '-key-material"']
    if complete:
        pieces.append("}}")
    events: list[BaseEvent] = []
    for i, piece in enumerate(pieces):
        chunk = {"index": 0, "args": piece, "id": "call" if i == 0 else None}
        if i == 0:
            chunk["name"] = "search"
        events.extend(
            calls.message(
                {"type": "AIMessageChunk", "tool_call_chunks": [chunk]},
                owner=None,
                parent_id="p",
                event_id=str(i),
            )
        )
    events.extend(calls.close(None))
    frames = _wire(events)
    assert "hidden" not in json.dumps(frames)
    assert "key-material" not in json.dumps(frames)
    args = [json.loads(frame["delta"]) for frame in frames if frame["type"] == "TOOL_CALL_ARGS"]
    if complete:
        assert args == [{"public": "ok", "creds": {"apiKey": "<redacted>"}}]
    else:
        assert not args


def test_command_message_lookup_requires_an_actual_matching_tool_message() -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="1")
    value = {
        "update": {
            "messages": [
                {"tool_call_id": "call", "type": "ai", "content": "private unrelated message"},
                {"tool_call_id": "call", "type": "tool", "content": "public result"},
            ]
        }
    }
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": value},
        owner=None,
        event_id="2",
    )
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == "public result"


def test_nested_binary_blocks_are_excluded_from_structured_tool_results() -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="1")
    output = {
        "title": "visible title",
        "preview": {"type": "image", "base64": "encoded-private-image", "mime_type": "image/png"},
        "attachments": [{"type": "audio", "base64": "encoded-private-audio"}],
        "nested": {"role": "assistant", "content": "encoded-private-message"},
        "state_change": {"update": {"messages": [{"content": "encoded-private-state"}]}},
        "mcp_app": {"payload": "encoded-private-widget"},
        "structuredContent": {"payload": "encoded-private-widget"},
    }
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": output},
        owner=None,
        event_id="2",
    )
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert "visible title" in result["content"]
    assert "encoded-private" not in result["content"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_nested_tool_message_failure_survives_duplicate_synthesized_result(wrapped: bool) -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(), owner=None, parent_id="p", event_id="proposal")
    failed = {"type": "tool", "tool_call_id": "call", "content": "검색 실패", "status": "error"}
    output = {"graph": None, "update": {"messages": [failed]}} if wrapped else failed
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": output},
        owner=None,
        event_id="native",
    )
    events.extend(calls.message(failed, owner=None, parent_id="unused", event_id="synthetic"))
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == "검색 실패"
    assert result["metadata"] == {"moldy": {"isError": True}}


def test_standalone_native_start_does_not_invent_an_assistant_parent() -> None:
    calls = PublicToolCalls("run", _allow)
    events = calls.tool(
        {"event": "tool-started", "tool_call_id": "call", "tool_name": "search", "input": {}},
        owner=None,
        event_id="started",
    )
    result_data = {"type": "tool", "tool_call_id": "call", "content": "결과"}
    events.extend(calls.message(result_data, owner=None, parent_id="unrelated", event_id="done"))
    assert calls.parent_id(None, "call") is None
    frames = _wire(events)
    assert frames[0]["type"] == "TOOL_CALL_START" and "parentMessageId" not in frames[0]
    assert frames[-1]["type"] == "TOOL_CALL_RESULT" and frames[-1]["content"] == "결과"


def test_late_provider_identity_can_bind_earlier_indexed_fragments() -> None:
    calls = PublicToolCalls("run", _allow)

    def chunk(**fields: Any) -> dict[str, Any]:
        return {"type": "AIMessageChunk", "tool_call_chunks": [{"index": 4, **fields}]}

    events = calls.message(chunk(args='{"q":"'), owner=None, parent_id="p", event_id="1")
    assert not events
    events += calls.message(
        chunk(id="late", name="search", args='seoul"}'), owner=None, parent_id="p", event_id="2"
    )
    events += calls.close(None)
    start, args, end = _wire(events)
    assert [start["type"], args["type"], end["type"]] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert args["toolCallId"] == "late" and json.loads(args["delta"]) == {"q": "seoul"}


@pytest.mark.parametrize(
    "row",
    [
        {"event": "content-block-start", "index": 0, "content": {"type": ["bad"], "id": "x"}},
        {"event": "content-block-delta", "index": 0, "delta": {"fields": {"type": ["bad"]}}},
    ],
)
def test_malformed_block_discriminators_are_ignored(row: dict[str, Any]) -> None:
    calls = PublicToolCalls("run", _allow)
    assert calls.message(row, owner=None, parent_id="parent", event_id="malformed") == []
    assert not calls.close(None)


def test_provider_result_message_id_is_stable_and_scoped_to_its_owner() -> None:
    def get_ids() -> tuple[str, str]:
        calls = PublicToolCalls("run", _allow)
        ids: list[str] = []
        for owner in (None, "child"):
            calls.message(_proposal(), owner=owner, parent_id="issuer", event_id="call")
            result = {
                "type": "tool",
                "id": "provider-result",
                "tool_call_id": "call",
                "content": "ok",
            }
            frames = calls.message(result, owner=owner, parent_id="unused", event_id="done")
            (returned,) = [f for f in _wire(frames) if f["type"] == "TOOL_CALL_RESULT"]
            ids.append(returned["messageId"])
        return ids[0], ids[1]

    root_id, child_id = get_ids()
    assert root_id == "provider-result"
    assert child_id != root_id
    assert get_ids() == (root_id, child_id)


def test_rename_before_a_chunk_receives_its_id_cannot_publish_private_arguments() -> None:
    calls = PublicToolCalls("r", lambda name, _id: name != "task")
    rows = [
        {"name": "task", "args": '{"description":"private task"}', "index": 0},
        {"id": "c", "name": "search", "args": "", "index": 0},
    ]
    frames: list[BaseEvent] = []
    for i, chunk in enumerate(rows):
        frames.extend(
            calls.message(
                {"type": "AIMessageChunk", "tool_call_chunks": [chunk]},
                owner=None,
                parent_id="p",
                event_id=str(i),
            )
        )
    frames.extend(
        calls.tool(
            {"event": "tool-started", "name": "search", "tool_call_id": "c"},
            owner=None,
            event_id="forged",
        )
    )
    frames.extend(calls.close(None))
    assert frames == []
