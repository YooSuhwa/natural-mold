"""Continuation metadata derived from the durable prefix of one ordinary chat run."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import orjson

from app.agent_runtime import event_names
from app.agent_runtime.chat_steer_model_delivery import steer_message_id
from app.agent_runtime.model_input_receipt import text_digest
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER


class RecoveryOutputStarted(ValueError):
    """Replaying this prefix could duplicate user-visible output."""

    code = "recovery_output_started"


type UsageKey = tuple[str | None, int, int, int, int, float | None]


_TOOL_BLOCKS = frozenset({"tool_call_chunk", "tool_call"})
_TOOL_PHASES = {"tool-started": "start", "tool-finished": "finish"}


def tool_event_key(event: Mapping[str, Any]) -> tuple[str, str] | None:
    data = event.get("data")
    if event.get("method") != "tools" or not isinstance(data, Mapping):
        return None
    name = data.get("event")
    phase = _TOOL_PHASES.get(name) if isinstance(name, str) else None
    call_id = data.get("tool_call_id")
    return (phase, call_id) if phase and isinstance(call_id, str) and call_id else None


def _tool_block_id(data: Mapping[str, Any]) -> str | None:
    content = data.get("content")
    if isinstance(content, Mapping) and content.get("type") in _TOOL_BLOCKS:
        call_id = content.get("id")
        if isinstance(call_id, str) and call_id:
            return call_id
    return None


def _message_has_output(data: Mapping[str, Any]) -> bool:
    event = data.get("event")
    if event in {"message-start", "message-finish"}:
        return False
    if event in {"content-block-start", "content-block-finish"}:
        block = data.get("content")
        if isinstance(block, Mapping):
            if block.get("type") in _TOOL_BLOCKS:
                return not _tool_block_id(data)
            if block.get("type") == "text":
                return bool(block.get("text"))
        return True
    if event == "content-block-delta":
        delta = data.get("delta")
        return not (isinstance(delta, Mapping) and delta.get("type") == "tool-call-delta")
    # A fallback AIMessageChunk can contain several blocks at once.
    if data.get("type") in {"ai", "AIMessage", "AIMessageChunk"}:
        return bool(data.get("content") or data.get("tool_call_chunks"))
    return True


def current_turn_messages(messages: list[Any]) -> list[Any]:
    """Visit from the latest human input, including only this turn's AI and tools."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, Mapping) and (message.get("type") or message.get("role")) in {
            "human",
            "user",
        }:
            # Count every tool or response since the original user turn. A steer can
            # expand this scan, never erase evidence that a side effect already ran.
            metadata = message.get("response_metadata")
            if isinstance(metadata, Mapping) and metadata.get(STEER_INPUT_MARKER) is True:
                continue
            return messages[index + 1 :]
    return messages


def state_output_proof(data: Mapping[str, Any], call_ids: set[str]) -> bool:
    if data.get("__interrupt__"):
        return True
    messages = data.get("messages")
    if isinstance(messages, list):
        for message in current_turn_messages(messages):
            if not isinstance(message, Mapping):
                return True
            if (message.get("type") or message.get("role")) not in {
                "ai",
                "assistant",
                "AIMessage",
                "AIMessageChunk",
            }:
                continue
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                return True
            ids = set()
            for call in calls:
                if (
                    not isinstance(call, Mapping)
                    or not isinstance(call.get("id"), str)
                    or not call["id"]
                ):
                    return True
                ids.add(call["id"])
            call_ids.update(ids)
            content = message.get("content")
            if not content:
                continue
            if not isinstance(content, list):
                return True
            if any(
                not isinstance(block, Mapping)
                or block.get("type") != "tool_call"
                or block.get("id") not in ids
                for block in content
            ):
                return True
    # The v3 updates channel nests state under values; fallback uses node names.
    return any(
        state_output_proof(value, call_ids) for value in data.values() if isinstance(value, Mapping)
    )


def recovery_output_started(
    event: Mapping[str, Any], *, verified_steer_events: frozenset[str] = frozenset()
) -> bool:
    """Check *before* state compaction, or read the proof from the committed event."""
    if event.get("recovery_output_started") is True or event.get("namespace"):
        return True
    method = event.get("method")
    data = event.get("data")
    if not isinstance(data, Mapping):
        return True
    if method == "messages":
        return _message_has_output(data)
    if method in {"values", "updates"}:
        return state_output_proof(data, set())
    if method == "tools":
        return tool_event_key(event) is None
    if method == "lifecycle":
        return data.get("event") not in {"running", "started"}
    if method == "custom":
        if data.get("name") == event_names.RUN_STEER and event.get("id") in verified_steer_events:
            return False
        return data.get("name") not in {
            "moldy.subagent_names",
            "moldy.memory_recalled",
            "moldy.skill_draft",
            "moldy.excluded_capabilities",
            "usage",
        }
    return method not in {"metadata", "tasks", "checkpoints"}


def _usage_key(data: Mapping[str, Any]) -> UsageKey:
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        raise RecoveryOutputStarted("Usage lacks provider snapshot")
    counts = ("prompt_tokens", "completion_tokens", "cache_creation_tokens", "cache_read_tokens")
    if any(type(payload.get(name)) is not int for name in counts):
        raise RecoveryOutputStarted("Usage lacks provider token counts")
    return (
        payload.get("assistant_msg_id"),
        payload["prompt_tokens"],
        payload["completion_tokens"],
        payload["cache_creation_tokens"],
        payload["cache_read_tokens"],
        payload.get("estimated_cost"),
    )


def state_fingerprint(data: Any) -> bytes:
    """Compare checkpoint replay by compacted state, independent of envelope ids."""
    return hashlib.sha256(orjson.dumps(data, option=orjson.OPT_SORT_KEYS)).digest()


def _verified_steer_message(
    event: Mapping[str, Any], expected: Mapping[str, str], *, run_id: str, thread_id: str
) -> str | None:
    """Recognize the root before-model HumanMessage already proved by SQL and checkpoint."""
    if event.get("method") != "messages" or event.get("namespace") != []:
        return None
    data = event.get("data")
    if not isinstance(data, Mapping) or data.keys() != {
        "id",
        "type",
        "content",
        "additional_kwargs",
        "response_metadata",
        "metadata",
    }:
        return None
    message_id, content, metadata = data["id"], data["content"], data["metadata"]
    if (
        data["type"] != "human"
        or not isinstance(message_id, str)
        or message_id not in expected
        or data["additional_kwargs"] != {}
        or data["response_metadata"] != {STEER_INPUT_MARKER: True}
        or not isinstance(content, str)
        or text_digest(content) != expected[message_id]
        or not isinstance(metadata, Mapping)
        or metadata.get("langgraph_node") != "RunSteerMiddleware.before_model"
        or metadata.get("thread_id") != thread_id
        or metadata.get("moldy_run_id", run_id) != run_id
    ):
        return None
    return message_id


@dataclass(frozen=True, slots=True)
class RecoveryStreamCursor:
    run_id: str
    thread_id: str
    last_seq: int
    event_ids: frozenset[str]
    tool_phases: frozenset[tuple[str, str]]
    visible_tool_call_ids: frozenset[str]
    last_values: bytes | None
    usage_keys: frozenset[UsageKey]
    claimed_steers: Mapping[str, str] = field(default_factory=dict)

    published_steer_messages: Mapping[str, str] = field(default_factory=dict)

    def already_published_steer_message(self, event: Mapping[str, Any]) -> bool:
        data = event.get("data")
        if event.get("method") != "messages" or not isinstance(data, Mapping):
            return False
        message_id = data.get("id")
        if not isinstance(message_id, str) or message_id not in self.published_steer_messages:
            return False
        if (
            _verified_steer_message(
                event, self.published_steer_messages, run_id=self.run_id, thread_id=self.thread_id
            )
            is None
        ):
            raise RecoveryOutputStarted("Changed steer message in recovered stream")
        return True

    @classmethod
    def from_events(
        cls,
        run_id: str,
        thread_id: str,
        events: Iterable[Mapping[str, Any]],
        *,
        verified_steer_events: frozenset[str] = frozenset(),
        claimed_steers: Mapping[str, str] | None = None,
    ) -> RecoveryStreamCursor:
        seq = -1
        seen: set[str] = set()
        phases: set[tuple[str, str]] = set()
        calls: set[str] = set()
        values = None
        usage: set[UsageKey] = set()
        expected = {
            steer_message_id(UUID(raw)): digest for raw, digest in (claimed_steers or {}).items()
        }
        published_messages: dict[str, str] = {}
        for event in events:
            steer_id = _verified_steer_message(event, expected, run_id=run_id, thread_id=thread_id)
            cursor = event.get("upstream_event_id") or event.get("id")
            event_seq = event.get("seq")
            if (
                event.get("thread_id") != thread_id
                or event.get("run_id") != run_id
                or type(event_seq) is not int
                or event_seq <= seq
                or not isinstance(cursor, str)
                or not cursor
                or cursor in seen
                or event.get("recovery_output_started") is True
                or (
                    steer_id is None
                    and recovery_output_started(event, verified_steer_events=verified_steer_events)
                )
                or steer_id in published_messages
            ):
                raise RecoveryOutputStarted("Unusable chat stream prefix")
            if steer_id is not None:
                published_messages[steer_id] = expected[steer_id]
            seen.add(cursor)
            seq = event_seq
            committed_ids = event.get("recovery_tool_call_ids", [])
            if not isinstance(committed_ids, list) or not all(
                isinstance(i, str) and i for i in committed_ids
            ):
                raise RecoveryOutputStarted("Unknown tool identity in stored state")
            calls.update(committed_ids)
            if key := tool_event_key(event):
                phases.add(key)
                calls.add(key[1])
            if event.get("method") == "custom" and event["data"].get("name") == "usage":
                usage.add(_usage_key(event["data"]))
            if event.get("method") == "values":
                values = state_fingerprint(event["data"])
            elif event.get("method") == "messages" and (block_id := _tool_block_id(event["data"])):
                calls.add(block_id)
        return cls(
            run_id,
            thread_id,
            seq,
            frozenset(seen),
            frozenset(phases),
            frozenset(calls),
            values,
            frozenset(usage),
            dict(claimed_steers or {}),
            published_messages,
        )


_cursor: ContextVar[RecoveryStreamCursor | None] = ContextVar(
    "recovery_stream_cursor", default=None
)


@contextmanager
def continue_protocol_delivery(cursor: RecoveryStreamCursor) -> Iterator[None]:
    token = _cursor.set(cursor)
    try:
        yield
    finally:
        _cursor.reset(token)


def recovery_stream_cursor(run_id: str, thread_id: str) -> RecoveryStreamCursor | None:
    cursor = _cursor.get()
    if cursor is not None and (cursor.run_id != run_id or cursor.thread_id != thread_id):
        raise RecoveryOutputStarted("Chat stream cursor belongs to a different execution")
    return cursor
