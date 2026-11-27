"""Project one chat run's visible tool calls and results onto the AG-UI 1.0 stream."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ag_ui.core import (
    BaseEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
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


def _derived_id(kind: str, run_id: str, owner: str | None, raw: str) -> str:
    identity = json.dumps(["ag-ui", "1.0", kind, run_id, owner, raw], ensure_ascii=False)
    return f"{kind}-{uuid5(NAMESPACE_URL, identity)}"


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
    args: Any = _ABSENT
    fragments: list[str] = field(default_factory=list)
    output_deltas: list[str] = field(default_factory=list)
    args_closed: bool = False
    result_sent: bool = False


class PublicToolCalls:
    """Folds provider model chunks, native tool events and synthesized copies by identity."""

    def __init__(
        self,
        run_id: str,
        allow_tool: Callable[[str | None, str], bool],
        *,
        secret_values: Iterable[str] | None = None,
        allow_scoped_tool: Callable[[str | None, str, str | None], bool] | None = None,
    ) -> None:
        self._run_id = run_id
        self._allow = allow_tool
        self._allow_scoped = allow_scoped_tool
        self._secrets = tuple(secret_values) if secret_values is not None else None
        self._calls: dict[tuple[str | None, str], _Call] = {}
        self._denied: set[tuple[str | None, str]] = set()
        self._slots: dict[tuple[str | None, str, int], _Slot] = {}

    def _authorized(self, owner: str | None, name: str, raw_id: str) -> bool:
        return self._allow(name, raw_id) and (
            self._allow_scoped is None or self._allow_scoped(name, raw_id, owner)
        )

    def _visible(self, owner: str | None, raw_id: str | None) -> _Call | None:
        if not raw_id or (owner, raw_id) in self._denied:
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

    def _open(
        self, owner: str | None, raw_id: str, name: str | None, parent_id: str | None
    ) -> tuple[_Call | None, list[BaseEvent]]:
        key = (owner, raw_id)
        existing = self._calls.get(key)
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
        public_id = raw_id if owner is None else _derived_id("tool", self._run_id, owner, raw_id)
        call = _Call(raw_id, public_id, name, parent_id, owner)
        self._calls[key] = call
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

    def tool(self, data: Mapping[str, Any], *, owner: str | None, event_id: str) -> list[BaseEvent]:
        raw_id = _identifier(data.get("tool_call_id"))
        if not raw_id:
            return []
        event = data.get("event")
        name = _identifier(data.get("name") or data.get("tool_name"))
        if event in ("tool-started", "tool-start", "start"):
            call, events = self._open(owner, raw_id, name, None)
            if call and not call.args_closed:
                call.args = data.get("args", data.get("input", call.args))
                events.extend(self._end_args(call))
            return events

        call = self._visible(owner, raw_id)
        if call is None or call.result_sent:
            return []
        if name and call.name != name:
            self._denied.add((owner, raw_id))
            return self._end_args(call, disclose=False)
        if event == "tool-output-delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                if call.name not in _PRIVATE_RESULTS:
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
        if call.name in _PRIVATE_RESULTS:
            # Memory writers return the original content and rationale inside JSON
            # text. They remain private even when the engine only supplies a string.
            result = REDACTED_MEMORY_FIELD
        elif failed:
            result = PUBLIC_TOOL_FAILURE
        else:
            content = _result_content(value)
            if content is _ABSENT:
                return events
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
        message_id = _identifier(message.get("id")) if message is not None else None
        if owner is None and message_id and message_id not in (call.public_id, call.parent_id):
            result_id = message_id
        else:
            result_id = _derived_id("result", self._run_id, owner, raw_id)
        events.append(
            ToolCallResultEvent(
                tool_call_id=call.public_id,
                message_id=result_id,
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
        for call in self._calls.values():
            if call.owner == owner and (parent_id is None or call.parent_id == parent_id):
                events.extend(
                    self._end_args(call, disclose=self._visible(owner, call.raw_id) is not None)
                )
        return events

    def close(self, owner: str | None) -> list[BaseEvent]:
        return self._close_args(owner, None)
