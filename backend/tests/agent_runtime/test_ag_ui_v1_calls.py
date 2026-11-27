"""Verify AG-UI tool messages from the persisted and streaming LangGraph event shapes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from ag_ui.core import BaseEvent
from langchain_core.tools import ToolException

from app.agent_runtime import event_names
from app.agent_runtime.ag_ui_v1_calls import PUBLIC_TOOL_FAILURE, PublicToolCalls
from app.agent_runtime.memory_event_projection import MEMORY_TOOL_NAMES
from app.agent_runtime.protocol_redaction import MEMORY_RESULT_TOOL_NAMES, REDACTED_MEMORY_FIELD
from app.agent_runtime.tools.agent_memo import _payload as agent_memo_result
from app.agent_runtime.tools.memory import _base_payload as memory_result
from app.tools.keys import DELEGATION_TOOL

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
    assert result["content"] == PUBLIC_TOOL_FAILURE == "도구 실행에 실패했습니다."
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


_MEMORY_NAMES = sorted(MEMORY_TOOL_NAMES | MEMORY_RESULT_TOOL_NAMES)
_MEM_CONTENT = "private preference from the source record"
_MEM_REASON = "private explanation from the source record"


def _memory_result_data(name: str) -> dict[str, Any]:
    if name == "propose_agent_memo":
        raw = agent_memo_result(event_names.MEMORY_PROPOSED, _MEM_CONTENT, _MEM_REASON)
        return json.loads(raw)
    return memory_result(
        memory_event=event_names.MEMORY_PROPOSED
        if name == "propose_memory"
        else event_names.MEMORY_SAVED,
        scope="agent" if name == "save_agent_memory" else "user",
        content=_MEM_CONTENT,
        reason=_MEM_REASON,
        policy="review",
        agent_id=None,
        conversation_id=None,
    )


@pytest.mark.parametrize("name", _MEMORY_NAMES)
@pytest.mark.parametrize(
    "shape", ["native-json", "native-object", "message-json", "command-object", "delta"]
)
def test_memory_tool_result_is_redacted_before_serializing_any_public_frame(
    name: str, shape: str
) -> None:
    calls = PublicToolCalls("run", _allow)
    events = calls.message(_proposal(name=name), owner=None, parent_id="issuer", event_id="call")
    payload = _memory_result_data(name)
    encoded = json.dumps(payload, ensure_ascii=False)
    if name == "search_memory" and shape == "delta":
        encoded = f"<memory_search_results>\n{_MEM_CONTENT}\n</memory_search_results>"
    content = payload if shape.endswith("object") else encoded
    message = {"type": "tool", "tool_call_id": "call", "content": content}
    finished: dict[str, Any] = {"event": "tool-finished", "tool_call_id": "call"}
    if shape == "message-json":
        events += calls.message(message, owner=None, parent_id="unused", event_id="message")
    else:
        if shape == "delta":
            for i, chunk in enumerate([encoded[:20], encoded[20:]]):
                events += calls.tool(
                    {"event": "tool-output-delta", "tool_call_id": "call", "delta": chunk},
                    owner=None,
                    event_id=str(i),
                )
        else:
            finished["output"] = (
                {"update": {"messages": [message]}} if shape == "command-object" else content
            )
        events += calls.tool(finished, owner=None, event_id="done")
    frames = _wire(events)
    (result,) = [frame for frame in frames if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == REDACTED_MEMORY_FIELD == "<redacted>"
    assert not any(marker in json.dumps(frames) for marker in (_MEM_CONTENT, _MEM_REASON))


@pytest.mark.parametrize("name", _MEMORY_NAMES)
def test_memory_tool_failure_is_redacted_and_still_reports_failure(name: str) -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(name=name), owner=None, parent_id="issuer", event_id="call")
    events = calls.tool(
        {"event": "tool-error", "tool_call_id": "call", "message": _MEM_REASON},
        owner=None,
        event_id="failed",
    )
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == REDACTED_MEMORY_FIELD
    assert result["metadata"] == {"moldy": {"isError": True}}


@pytest.mark.parametrize("alteration", ["unknown", "revoked", "wrong inner name", "wrong inner id"])
def test_unattributable_nested_memory_results_are_withheld(alteration: str) -> None:
    accepted = {"call"}
    calls = PublicToolCalls("run", lambda _name, raw_id: raw_id in accepted)
    calls.message(_proposal(name="search"), owner=None, parent_id="issuer", event_id="call")
    raw_id = "unknown" if alteration == "unknown" else "call"
    if alteration == "revoked":
        accepted.clear()
    message = {
        "type": "tool",
        "tool_call_id": "other" if alteration == "wrong inner id" else raw_id,
        "name": "search_memory" if alteration == "wrong inner name" else "search",
        "content": _MEM_CONTENT,
    }
    events = calls.tool(
        {
            "event": "tool-finished",
            "tool_call_id": raw_id,
            "output": {"update": {"messages": [message]}},
        },
        owner=None,
        event_id="native",
    )
    assert events == []


def test_successful_ordinary_tool_preserves_its_authored_result() -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(name="get_settings"), owner=None, parent_id="issuer", event_id="call")
    content = {"content": "a public setting", "reason": "chosen for display"}
    events = calls.tool(
        {"event": "tool-finished", "tool_call_id": "call", "output": content},
        owner=None,
        event_id="done",
    )
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert json.loads(result["content"]) == content


def test_mcp_tool_exception_cannot_publish_the_sdk_binary_block_representation() -> None:
    calls = PublicToolCalls("run", _allow)
    calls.message(_proposal(name="mcp_image"), owner=None, parent_id="issuer", event_id="call")
    # app.mcp.sdk.as_langchain_tool uses str(blocks) when an error result has no text.
    blocks = [{"type": "image", "base64": "private-binary-body", "mime_type": "image/png"}]
    error = ToolException(str(blocks))
    events = calls.tool(
        {"event": "tool-error", "tool_call_id": "call", "message": str(error)},
        owner=None,
        event_id="failure",
    )
    (result,) = [frame for frame in _wire(events) if frame["type"] == "TOOL_CALL_RESULT"]
    assert result["content"] == PUBLIC_TOOL_FAILURE
    assert result["metadata"] == {"moldy": {"isError": True}}
    assert "private-binary-body" not in json.dumps(result)


def test_delegation_identity_requires_a_currently_visible_task_call_in_the_same_owner() -> None:
    permitted = {"delegated", "ordinary"}
    calls = PublicToolCalls("run", lambda _name, raw_id: raw_id in permitted)
    calls.message(_proposal("ordinary", "search"), owner=None, parent_id="assistant", event_id="1")
    calls.message(
        _proposal("delegated", DELEGATION_TOOL),
        owner="child",
        parent_id="assistant-child",
        event_id="2",
    )
    assert calls.delegation_id(None, "ordinary") is None
    assert calls.delegation_id(None, "delegated") is None
    assert calls.delegation_id("child", "unknown") is None
    assert calls.delegation_id("child", "delegated") == calls.public_id("child", "delegated")
    assert calls.delegation_id("child", "delegated") is not None
    permitted.clear()
    assert calls.delegation_id("child", "delegated") is None


def test_tool_identity_and_owner_policy_must_both_authorize_a_delegation() -> None:
    allowed_owners = {"permitted"}
    calls = PublicToolCalls(
        "run",
        lambda _name, raw_id: raw_id == "same-id",
        allow_scoped_tool=lambda _name, _raw_id, owner: owner in allowed_owners,
    )

    def propose(owner: str, raw_id: str) -> list[BaseEvent]:
        return calls.message(
            _proposal(raw_id, DELEGATION_TOOL), owner=owner, parent_id="issuer", event_id=raw_id
        )

    assert not propose("permitted", "globally-denied")
    assert not propose("denied-sibling", "same-id")
    assert calls.public_id("denied-sibling", "same-id") is None
    assert calls.delegation_id("denied-sibling", "same-id") is None
    assert propose("permitted", "same-id")
    assert calls.public_id("permitted", "same-id") == calls.delegation_id("permitted", "same-id")
    assert calls.delegation_id("permitted", "same-id") is not None

    allowed_owners.clear()
    assert calls.public_id("permitted", "same-id") is None
    assert calls.delegation_id("permitted", "same-id") is None
    assert not calls.tool(
        {"event": "tool-finished", "tool_call_id": "same-id", "output": "private child output"},
        owner="permitted",
        event_id="unnamed",
    )
