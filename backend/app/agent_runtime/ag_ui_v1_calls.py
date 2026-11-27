"""Project one chat run's visible tool calls and results onto the AG-UI 1.0 stream."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ag_ui.core import (
    BaseEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from app.agent_runtime.ag_ui_v1_issuance import (
    ToolContinuation,
    public_tool_call_id,
    public_tool_result_id,
)
from app.agent_runtime.ag_ui_v1_memo_files import (
    private_memo_file_call,
    public_grep_result,
    redacted_file_args,
)
from app.agent_runtime.memory_event_projection import MEMORY_TOOL_NAMES
from app.agent_runtime.message_utils import content_to_text
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_redaction import MEMORY_RESULT_TOOL_NAMES, REDACTED_MEMORY_FIELD
from app.tools.keys import DELEGATION_TOOL

# Tool exceptions may include stringified binary MCP blocks or provider diagnostics.
PUBLIC_TOOL_FAILURE = "도구 실행에 실패했습니다."
_PRIVATE_RESULTS = MEMORY_TOOL_NAMES | MEMORY_RESULT_TOOL_NAMES
_ABSENT = object()
_TOOL_BLOCKS = ("tool_call", "tool_call_chunk", "server_tool_call", "server_tool_call_chunk")
_PRIVATE_RESULT_KEYS = {
    "_meta",
    "additional_kwargs",
    "artifact",
    "checkpoint",
    "checkpoint_ns",
    "internal_state",
    "kwargs",
    "metadata",
    "mcp_app",
    "raw",
    "structured_content",
    "structuredContent",
    "rawEvent",
    "raw_event",
    "reasoning",
    "response_metadata",
    "state",
    "stack",
    "stacktrace",
    "thinking",
    "traceback",
}
_PRIVATE_BLOCK_TYPES = {
    "thinking",
    "reasoning",
    "reasoning_content",
    "redacted_thinking",
    "analysis",
    "signature",
    "image",
    "image_url",
    "audio",
    "video",
    "document",
    "file",
    "data",
    "ai",
    "AIMessage",
    "AIMessageChunk",
    "SystemMessage",
    "HumanMessage",
    "system",
    "human",
}


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _public_value(value: Any) -> Any:
    """Keep explicitly authored tool data, without model or transport internals."""
    if isinstance(value, Mapping):
        if value.get("role") in ("assistant", "system", "developer", "human", "user"):
            return _ABSENT
        if any(key in value for key in ("graph", "update", "resume", "goto")):
            return _ABSENT
        block_type = value.get("type")
        if isinstance(block_type, str):
            if block_type in _PRIVATE_BLOCK_TYPES:
                return _ABSENT
            if block_type == "text":
                text = value.get("text")
                return text if isinstance(text, str) else _ABSENT
            if block_type == "tool_result":
                return _public_value(value.get("content", _ABSENT))
        return {
            key: safe
            for key, item in value.items()
            if isinstance(key, str)
            and key not in _PRIVATE_RESULT_KEYS
            and (safe := _public_value(item)) is not _ABSENT
        }
    if isinstance(value, list):
        return [safe for item in value if (safe := _public_value(item)) is not _ABSENT]
    return value if value is None or isinstance(value, str | int | float | bool) else _ABSENT


def _tool_message(data: Mapping[str, Any]) -> bool:
    return data.get("type") in ("tool", "ToolMessage") or data.get("role") == "tool"


def _result_message(output: Any, raw_id: str) -> Mapping[str, Any] | None:
    """Find only the ToolMessage for this call inside a native result or Command."""
    if not isinstance(output, Mapping):
        return None
    if _tool_message(output):
        return output if output.get("tool_call_id") == raw_id else None
    update = output.get("update")
    messages = update.get("messages") if isinstance(update, Mapping) else None
    if isinstance(messages, list):
        for item in messages:
            if (
                isinstance(item, Mapping)
                and _tool_message(item)
                and item.get("tool_call_id") == raw_id
            ):
                return item
    return None


def _result_content(value: Any) -> Any:
    if isinstance(value, list) and any(
        isinstance(item, Mapping) and "type" in item for item in value
    ):
        return content_to_text(value)
    return _public_value(value)


@dataclass(slots=True)
class _Slot:
    """Providers can deliver an indexed argument delta before the tool identity."""

    raw_id: str | None = None
    name: str | None = None
    conflicting_names: bool = False
    fragments: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Call:
    raw_id: str
    public_id: str
    name: str
    parent_id: str | None
    owner: str | None
    source_message_id: str | None = None
    args: Any = _ABSENT
    fragments: list[str] = field(default_factory=list)
    output_deltas: list[str] = field(default_factory=list)
    args_closed: bool = False
    result_sent: bool = False
    private_file: bool = False

    @property
    def private_result(self) -> bool:
        return self.private_file or self.name in _PRIVATE_RESULTS


class PublicToolCalls:
    """Folds provider model chunks, native tool events and synthesized copies by identity."""

    def __init__(
        self,
        run_id: str,
        allow_tool: Callable[[str | None, str], bool],
        *,
        secret_values: Iterable[str] | None = None,
        tool_continuations: Iterable[ToolContinuation] = (),
        allow_scoped_tool: Callable[[str | None, str, str | None], bool] | None = None,
    ) -> None:
        self._run_id = run_id
        self._allow = allow_tool
        self._allow_scoped = allow_scoped_tool
        self._secrets = tuple(secret_values) if secret_values is not None else None
        self._calls: dict[tuple[str | None, str], _Call] = {}
        self._model_sources: dict[tuple[str | None, str], str] = {}
        self._issuers: dict[tuple[str | None, str, str | None], _Call] = {}
        self._ambiguous: set[tuple[str | None, str]] = set()
        # Multiple outstanding issuances under a provider-local ID are ambiguous.
        # Only a unique, server-proven predecessor can supply its original identity.
        self._continuations: dict[tuple[str | None, str], ToolContinuation] = {}
        ambiguous: set[tuple[str | None, str]] = set()
        for continuation in tool_continuations:
            key = (continuation.owner, continuation.raw_id)
            if key in self._continuations and continuation != self._continuations[key]:
                ambiguous.add(key)
            self._continuations[key] = continuation
        for key in ambiguous:
            del self._continuations[key]
        self._denied: set[tuple[str | None, str]] = set()
        self._slots: dict[tuple[str | None, str, int], _Slot] = {}

    def _authorized(self, owner: str | None, name: str, raw_id: str) -> bool:
        return self._allow(name, raw_id) and (
            self._allow_scoped is None or self._allow_scoped(name, raw_id, owner)
        )

    def _visible(self, owner: str | None, raw_id: str | None) -> _Call | None:
        if not raw_id or (owner, raw_id) in self._denied or (owner, raw_id) in self._ambiguous:
            return None
        call = self._calls.get((owner, raw_id))
        if call is not None and self._authorized(owner, call.name, raw_id):
            return call
        return None

    def public_id(self, owner: str | None, raw_id: str | None) -> str | None:
        call = self._visible(owner, raw_id)
        return call.public_id if call else None

    def delegation_id(self, owner: str | None, raw_id: str | None) -> str | None:
        call = self._visible(owner, raw_id)
        return call.public_id if call and call.name == DELEGATION_TOOL else None

    def parent_id(self, owner: str | None, raw_id: str | None) -> str | None:
        call = self._visible(owner, raw_id)
        return call.parent_id if call else None

    @property
    def root_issuances(self) -> dict[tuple[str | None, str], str]:
        return {
            (call.parent_id, call.raw_id): call.public_id
            for call in self._issuers.values()
            if call.owner is None
            and (None, call.raw_id) not in self._denied
            and self._authorized(None, call.name, call.raw_id)
        }

    @property
    def unresolved_tool_calls(self) -> tuple[ToolContinuation, ...]:
        pending = self._continuations.copy()
        for key, call in self._calls.items():
            if not call.result_sent and key not in self._denied:
                pending[key] = ToolContinuation(
                    call.owner,
                    call.raw_id,
                    call.name,
                    call.public_id,
                    call.parent_id,
                    call.source_message_id,
                    call.private_result,
                )
        return tuple(
            item
            for key, item in pending.items()
            if key not in self._denied
            and key not in self._ambiguous
            and self._authorized(item.owner, item.name, item.raw_id)
        )

    def _open(
        self, owner: str | None, raw_id: str, name: str | None, parent_id: str | None
    ) -> tuple[_Call | None, list[BaseEvent]]:
        key = (owner, raw_id)
        existing = self._calls.get(key)
        if parent_id:
            issuer = (owner, raw_id, parent_id)
            if issuer in self._issuers and (not existing or existing.parent_id != parent_id):
                return None, []  # A prior message snapshot arrived after the next issuance.
            if (
                existing
                and existing.parent_id != parent_id
                and (existing.result_sent or existing.parent_id)
            ):
                if not existing.result_sent:
                    # Without an identifying message, a native result cannot
                    # choose between two simultaneously outstanding issuances.
                    self._ambiguous.add(key)
                existing = None
                self._denied.discard(key)
        if key in self._denied:
            return None, []
        if existing:
            if (name and existing.name != name) or not self._authorized(
                owner, existing.name, raw_id
            ):
                self._denied.add(key)
                return None, self._end_args(existing, disclose=False)
            return existing, []
        if not name:
            return None, []
        if not self._authorized(owner, name, raw_id):
            self._denied.add(key)
            return None, []
        original = self._continuations.pop(key, None)
        carried = parent_id is None and original is not None and original.name == name
        if carried and original is not None:
            public_id = original.public_id
            parent_id = original.parent_message_id
            source_id = original.source_message_id
        else:
            public_id = public_tool_call_id(self._run_id, owner, parent_id, raw_id)
            source_id = self._model_sources.get((owner, parent_id)) if parent_id else None
        call = _Call(raw_id, public_id, name, parent_id, owner, source_message_id=source_id)
        if carried and original is not None:
            call.private_file = original.private_result
        self._calls[key] = call
        self._issuers[owner, raw_id, parent_id] = call
        return call, [
            ToolCallStartEvent(
                tool_call_id=public_id,
                tool_call_name=name,
                parent_message_id=parent_id,
                subagent_run_id=owner,
            )
        ]

    def _end_args(self, call: _Call, *, disclose: bool = True) -> list[BaseEvent]:
        if call.args_closed:
            return []
        call.args_closed = True
        events: list[BaseEvent] = []
        if disclose:
            args = call.args if call.args is not _ABSENT else "".join(call.fragments)
            # Buffer fragments until complete so splitting a secret across two model
            # chunks does not defeat key- or value-based redaction.
            if isinstance(args, str) and args:
                try:
                    args = json.loads(args)
                except (ValueError, RecursionError):
                    # Never publish partial argument strings that could contain a
                    # sensitive field whose value cannot yet be parsed.
                    args = _ABSENT
            call.private_file |= private_memo_file_call(call.name, args)
            if call.private_file:
                args = redacted_file_args(call.name)
            if args is not _ABSENT and args != "":
                safe = project_and_redact_protocol_data(
                    "tools",
                    {"name": call.name, "tool_call_id": call.raw_id, "args": args},
                    secret_values=self._secrets,
                )["args"]
                events.append(
                    ToolCallArgsEvent(
                        tool_call_id=call.public_id,
                        delta=json.dumps(safe, ensure_ascii=False, separators=(",", ":")),
                        subagent_run_id=call.owner,
                    )
                )
        call.fragments.clear()
        call.args = _ABSENT
        events.append(ToolCallEndEvent(tool_call_id=call.public_id, subagent_run_id=call.owner))
        return events

    def _model_call(
        self,
        block: Mapping[str, Any],
        owner: str | None,
        parent_id: str,
        index: int,
        *,
        fragment: bool = False,
        finish: bool = False,
    ) -> list[BaseEvent]:
        key = (owner, parent_id, index)
        slot = self._slots.setdefault(key, _Slot())
        incoming_id = _identifier(block.get("id"))
        if incoming_id and slot.raw_id and incoming_id != slot.raw_id:
            slot = self._slots[key] = _Slot()
        slot.raw_id = incoming_id or slot.raw_id
        name = _identifier(block.get("name"))
        if name and slot.name and name != slot.name:
            slot.conflicting_names = True
        if slot.conflicting_names:
            slot.fragments.clear()
            if slot.raw_id:
                self._denied.add((owner, slot.raw_id))
                call = self._calls.get((owner, slot.raw_id))
                if call is not None:
                    return self._end_args(call, disclose=False)
            return []
        slot.name = name or slot.name
        args = block.get("args", _ABSENT)
        if fragment and isinstance(args, str):
            slot.fragments.append(args)
        if not slot.raw_id:
            return []
        call, events = self._open(owner, slot.raw_id, slot.name, parent_id)
        if call is None:
            if (owner, slot.raw_id) in self._denied:
                slot.fragments.clear()
            return events
        if not call.args_closed:
            call.fragments.extend(slot.fragments)
            if not (fragment and isinstance(args, str)) and args is not _ABSENT:
                call.args = args
        slot.fragments.clear()
        if finish:
            events.extend(self._end_args(call))
        return events

    def message(
        self,
        data: Mapping[str, Any],
        *,
        owner: str | None,
        parent_id: str,
        event_id: str,
    ) -> list[BaseEvent]:
        if _tool_message(data):
            return self.tool(
                {**data, "event": "tool-finished"},
                owner=owner,
                event_id=event_id,
            )
        source_id = _identifier(data.get("message_id") or data.get("id"))
        if source_id:
            self._model_sources[owner, parent_id] = source_id
        event = data.get("event")
        index = data.get("index", 0)
        if event in ("content-block-start", "content-block-finish") and isinstance(index, int):
            block = data.get("content", data.get("content_block"))
            if isinstance(block, Mapping) and block.get("type") in _TOOL_BLOCKS:
                is_start = event == "content-block-start"
                # An empty object at block-start is a skeleton, not the final args.
                initial = {
                    k: v for k, v in block.items() if not (is_start and k == "args" and v == {})
                }
                return self._model_call(
                    initial, owner, parent_id, index, fragment=is_start, finish=not is_start
                )
        if event == "content-block-delta" and isinstance(index, int):
            delta = data.get("delta")
            if isinstance(delta, Mapping):
                if delta.get("type") == "tool-call-delta":
                    return self._model_call(delta, owner, parent_id, index, fragment=True)
                fields = delta.get("fields")
                if isinstance(fields, Mapping) and fields.get("type") in _TOOL_BLOCKS:
                    return self._model_call(fields, owner, parent_id, index, fragment=True)
        if event == "message-finish":
            return self._close_args(owner, parent_id)
        chunks = data.get("tool_call_chunks")
        fragments = isinstance(chunks, list) and bool(chunks)
        calls = chunks if fragments else data.get("tool_calls")
        events: list[BaseEvent] = []
        if isinstance(calls, list):
            for position, block in enumerate(calls):
                if not isinstance(block, Mapping):
                    continue
                idx = block.get("index")
                events += self._model_call(
                    block,
                    owner,
                    parent_id,
                    idx if isinstance(idx, int) else position,
                    fragment=fragments,
                    finish=not fragments and data.get("type") not in ("AIMessageChunk", "ai_chunk"),
                )
        return events

    def permits_synthetic(self, owner: str | None, raw_id: object, source_id: str | None) -> bool:
        if not source_id or not isinstance(raw_id, str):
            return False
        call = self._visible(owner, raw_id)
        if call:
            return call.source_message_id == source_id
        continuation = self._continuations.get((owner, raw_id))
        return bool(continuation and continuation.source_message_id == source_id)

    def tool(self, data: Mapping[str, Any], *, owner: str | None, event_id: str) -> list[BaseEvent]:
        raw_id = _identifier(data.get("tool_call_id"))
        if not raw_id:
            return []
        event = data.get("event")
        name = _identifier(data.get("name") or data.get("tool_name"))
        if event in ("tool-started", "tool-start", "start"):
            call, events = self._open(owner, raw_id, name, None)
            if call:
                args = data.get("args", data.get("input", _ABSENT))
                if call.args_closed and args is not _ABSENT:
                    # The executed path can differ from the model's proposal after review.
                    call.private_file |= private_memo_file_call(call.name, args)
                elif not call.args_closed:
                    if args is not _ABSENT:
                        call.args = args
                    events.extend(self._end_args(call))
            return events

        call = self._visible(owner, raw_id)
        if call is None and event in (
            "tool-finished",
            "tool-result",
            "finish",
            "tool-error",
            "error",
        ):
            # Some graph versions resume directly with a ToolMessage. A matching
            # current predecessor proof supplies the issuer; result content alone
            # cannot establish a tool's identity.
            original = self._continuations.get((owner, raw_id))
            if original and (name is None or name == original.name):
                call, opened = self._open(owner, raw_id, original.name, None)
                if call:
                    return [*opened, *self.tool(data, owner=owner, event_id=event_id)]
        if call is None or call.result_sent:
            return []
        if name and call.name != name:
            self._denied.add((owner, raw_id))
            return self._end_args(call, disclose=False)
        if event == "tool-output-delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                if not call.private_result:
                    call.output_deltas.append(delta)
                elif not call.output_deltas:
                    call.output_deltas.append(REDACTED_MEMORY_FIELD)
            return []
        if event not in ("tool-finished", "tool-result", "finish", "tool-error", "error"):
            return []
        failed = event in ("tool-error", "error") or data.get("status") == "error"
        fields = (
            ("message", "error", "content", "output", "result")
            if failed
            else ("content", "output", "result")
        )
        value = next((data[k] for k in fields if k in data), _ABSENT)
        message = _result_message(data if _tool_message(data) else value, raw_id)
        if message is not None:
            claimed = _identifier(message.get("name"))
            if claimed and claimed != call.name:
                self._denied.add((owner, raw_id))
                return self._end_args(call, disclose=False)
            value = message.get("content", _ABSENT)
            failed |= message.get("status") == "error"
        elif isinstance(value, Mapping) and (
            _tool_message(value)
            or any(key in value for key in ("graph", "update", "resume", "goto"))
        ):
            # Command updates may contain unrelated state, private child output and
            # other calls' messages. Only the matching ToolMessage is attributable.
            return []
        events = self._end_args(call)
        if value is _ABSENT and call.output_deltas:
            value = "".join(call.output_deltas)
        if value is _ABSENT and not failed:
            return events
        if call.private_result:
            # Memory writers return the original content and rationale inside JSON
            # text. They remain private even when the engine only supplies a string.
            result = REDACTED_MEMORY_FIELD
        elif failed:
            result = PUBLIC_TOOL_FAILURE
        else:
            content = _result_content(value)
            if content is _ABSENT:
                return events
            if call.name == "grep":
                content = public_grep_result(content)
            public = project_and_redact_protocol_data(
                "tools",
                {"name": call.name, "tool_call_id": raw_id, "content": content},
                secret_values=self._secrets,
            )["content"]
            result = (
                public
                if isinstance(public, str)
                else json.dumps(public, ensure_ascii=False, separators=(",", ":"))
            )
        events.append(
            ToolCallResultEvent(
                tool_call_id=call.public_id,
                message_id=public_tool_result_id(call.public_id),
                content=result,
                metadata={"moldy": {"isError": True}} if failed else None,
                subagent_run_id=owner,
            )
        )
        call.result_sent = True
        call.output_deltas.clear()
        return events

    def _close_args(self, owner: str | None, parent_id: str | None) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        for key in list(self._slots):
            if key[0] == owner and (parent_id is None or key[1] == parent_id):
                del self._slots[key]
        for call in self._issuers.values():
            if call.owner == owner and (parent_id is None or call.parent_id == parent_id):
                events.extend(
                    self._end_args(call, disclose=self._visible(owner, call.raw_id) is not None)
                )
        return events

    def close(self, owner: str | None) -> list[BaseEvent]:
        return self._close_args(owner, None)
