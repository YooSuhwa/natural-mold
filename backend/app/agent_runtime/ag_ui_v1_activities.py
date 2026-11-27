"""Project persisted chat events into public, replayable activity messages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from ag_ui.core import (
    ActivitySnapshotEvent,
    BaseEvent,
    StateSnapshotEvent,
    TextMessageStartEvent,
    TokenUsage,
    ToolCallStartEvent,
)

from app.agent_runtime.ag_ui_v1_activity_payloads import (
    canonical_uuid,
    nonempty,
    public_agent_memo,
    public_compaction,
    public_exclusions,
    public_file,
    public_memory,
    public_recall,
    public_todos,
    public_usage,
    widget_pointer,
)
from app.agent_runtime.ag_ui_v1_card_props import (
    public_skill_draft,
    public_skill_validation,
    public_ui_data,
)
from app.agent_runtime.ag_ui_v1_usage import RunUsage
from app.agent_runtime.chat_steer_protocol import valid_stored_steer
from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES
from app.agent_runtime.protocol_events import StoredProtocolEvent

_PROCESSORS = {
    "compaction": public_compaction,
    "excluded_capabilities": public_exclusions,
    "memory_recalled": public_recall,
    "skill_draft": public_skill_draft,
    "skill_validation": public_skill_validation,
    "ui_data": public_ui_data,
    "usage": public_usage,
    **dict.fromkeys(MEMORY_EVENT_NAMES, public_memory),
    "agent_memo_proposed": public_agent_memo,
}
PUBLIC_ACTIVITY_TYPES: frozenset[str] = frozenset(
    f"moldy.{kind}"
    for kind in (
        *_PROCESSORS,
        "file_event",
        "subagent_names",
        "run_steer",
        "mcp_app",
        "subagent_todos",
    )
)


def _custom(event: StoredProtocolEvent) -> tuple[str, Mapping[str, Any]] | None:
    method, data = event["method"], event["data"]
    if not isinstance(data, Mapping):
        return None
    if method == "custom":
        name = nonempty(data.get("name") or data.get("channel"))
    elif method.startswith("custom:"):
        name = method.removeprefix("custom:")
    else:
        return None
    if not name:
        return None
    payload = data.get("payload", data)
    return (name.removeprefix("moldy."), payload) if isinstance(payload, Mapping) else None


def _public_card(
    name: str,
    payload: Mapping[str, Any],
    event: StoredProtocolEvent,
    readable_versions: frozenset[tuple[str, str]],
) -> tuple[str, dict[str, Any]] | None:
    if payload.get("run_id") not in (None, event["run_id"]):
        return None
    if name == "file_event":
        card = public_file(payload, event, readable_versions)
    elif name == "usage":
        # The native side-effect writer binds receipts to a single source ID,
        # namespace, and physical run. Older journals used a direct stream method.
        data = event["data"]
        current = (
            event["method"] == "custom"
            and data.keys() == {"name", "payload"}
            and data["name"] == "usage"
        )
        legacy = event["method"] == "custom:usage" and "payload" not in data
        if not (current or legacy) or not (
            payload.get("run_id") == event["run_id"]
            and event["upstream_event_id"] == event["id"]
            and event["id"].endswith(":usage")
        ):
            return None
        card = public_usage(payload)
    elif name == "ui_data":
        # The public attachment keys are calculated from the issued tool and
        # visible message. Unknown spellings must not turn a claimed attachment
        # into a seemingly independent card.
        if any(
            key in payload
            for key in (
                "toolCallId",
                "messageId",
                "assistant_msg_id",
                "public_tool_call_id",
                "public_message_id",
            )
        ):
            return None
        card = public_ui_data(payload)
        if card and not card.get("tool_call_id") and not card.get("message_id"):
            # Native tool side-effects carry an indexed receipt. A damaged
            # receipt with no attachment cannot be shown as an independent card.
            _, sep, index = event["id"].rpartition(":ui_data:")
            if sep and index.isdecimal():
                return None
    elif name == "run_steer":
        thread = canonical_uuid(event["thread_id"])
        if (
            event["method"] != "custom"
            or not thread
            or not valid_stored_steer(event, conversation_id=UUID(thread), run_id=event["run_id"])
        ):
            return None
        # valid_stored_steer verifies the exact four-field schema and identity.
        card = dict(payload)
    elif name == "subagent_names":
        names = payload.get("names")
        if not isinstance(names, Mapping):
            return None
        labels = {key: value for key, value in names.items() if nonempty(key) and nonempty(value)}
        card = {"names": labels} if labels else None
    else:
        processor = _PROCESSORS.get(name)
        if processor is None:
            return None
        card = processor(payload)
    return (name, card) if card else None


def _entity_key(name: str, content: dict[str, Any], source: StoredProtocolEvent) -> list[str]:
    if name == "ui_data":
        # One tool result can emit multiple cards of the same type. Its stored
        # event ID includes the stable element index assigned by the producer.
        return [source["id"]]
    if name == "usage":
        return [content.get("public_message_id") or content.get("assistant_msg_id") or source["id"]]
    if name == "mcp_app":
        return [content["tool_call_id"], content["binding_id"]]
    if name == "file_event":
        return [content["id"]]
    if name == "run_steer":
        return [content["steer_id"]]
    if name in MEMORY_EVENT_NAMES:
        return [content.get("id") or content.get("proposal_id") or source["id"]]
    return []


class PublicActivities:
    """One instance per run. Only allowlisted content ever enters the public transcript."""

    def __init__(
        self,
        *,
        readable_artifact_versions: frozenset[tuple[str, str]] = frozenset(),
        readable_widget_bindings: frozenset[tuple[str, str]] | None = None,
    ) -> None:
        self._readable_versions = readable_artifact_versions
        self._readable_widget_bindings = readable_widget_bindings or frozenset()
        self._usage = RunUsage()
        self._visible_messages: set[tuple[str | None, str]] = set()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._states: dict[str | None, dict[str, Any]] = {}

    def observe_visible_messages(self, frames: Sequence[BaseEvent]) -> None:
        """Only emitted model text and tools establish a card's public attachment."""
        for frame in frames:
            if isinstance(frame, TextMessageStartEvent):
                self._visible_messages.add((frame.subagent_run_id, frame.message_id))
            elif isinstance(frame, ToolCallStartEvent) and frame.parent_message_id:
                self._visible_messages.add((frame.subagent_run_id, frame.parent_message_id))

    def observe_values_parent(
        self, event: StoredProtocolEvent, *, subagent_run_id: str | None
    ) -> None:
        """Recognize a usage receipt's source before removing private checkpoint state."""
        if isinstance(event["data"], Mapping):
            self._usage.observe_values_parent(
                event["data"], event_id=event["id"], subagent_run_id=subagent_run_id
            )

    def terminal_usage(self) -> list[TokenUsage] | None:
        return self._usage.terminal_usage()

    def project(
        self,
        event: StoredProtocolEvent,
        *,
        run_id: str,
        subagent_run_id: str | None = None,
        public_message_id: str | None = None,
        public_tool_call_id: str | None = None,
    ) -> list[BaseEvent]:
        data = event["data"]
        method = event["method"]
        if method == "messages" and isinstance(data, (tuple, list)) and len(data) == 2:
            # The archived stream-mode writer stored (message, metadata).
            message, metadata = data
            if isinstance(message, Mapping) and isinstance(metadata, Mapping):
                data = {**message, "metadata": metadata}
                event = {**event, "data": data}
        if not isinstance(data, Mapping):
            return []
        if method in ("values", "updates"):
            state = public_todos(data)
            if state is None or state == self._states.get(subagent_run_id):
                return []
            self._states[subagent_run_id] = state
            if subagent_run_id is None:
                return [StateSnapshotEvent(snapshot=state)]
            name, content = "subagent_todos", state

        elif method in ("messages", "tools"):
            assistant = data.get("type") in ("ai", "assistant", "AIMessage", "AIMessageChunk")
            start = data.get("event") == "message-start" and data.get("role") in ("assistant", "ai")
            finish = data.get("event") == "message-finish"
            if method == "messages" and public_message_id and (assistant or start or finish):
                self._usage.observe_message(
                    data,
                    subagent_run_id=subagent_run_id,
                    public_message_id=public_message_id,
                    event_id=event["id"],
                )
            pointer = widget_pointer(event)
            if (
                not pointer
                or not public_tool_call_id
                or (pointer["tool_call_id"], pointer["binding_id"])
                not in self._readable_widget_bindings
            ):
                return []
            name, content = "mcp_app", pointer
        else:
            custom = _custom(event)
            if not custom or not (card := _public_card(*custom, event, self._readable_versions)):
                return []
            name, content = card
        if name == "usage":
            # The v3 finish event may omit the model's source ID. Its uniquely
            # derived usage receipt can still prove which public message incurred it.
            public_message_id = self._usage.observe_usage(
                content,
                subagent_run_id=subagent_run_id,
                public_message_id=public_message_id,
                event_id=event["id"],
            )
            if (
                not public_message_id
                or (subagent_run_id, public_message_id) not in self._visible_messages
            ):
                return []
            if "assistant_msg_id" in content:
                content["assistant_msg_id"] = public_message_id
        if name == "ui_data":
            if content.get("tool_call_id"):
                if not public_tool_call_id:
                    return []
                content["tool_call_id"] = public_tool_call_id
            if content.get("message_id"):
                if not public_message_id:
                    return []
                content["message_id"] = public_message_id
        if name in ("ui_data", "usage"):
            content["run_id"] = run_id
        if public_tool_call_id and name in ("ui_data", "mcp_app") and content.get("tool_call_id"):
            content["public_tool_call_id"] = public_tool_call_id
        if public_message_id and name in ("usage", "file_event", "ui_data", "mcp_app"):
            content["public_message_id"] = public_message_id

        key = json.dumps(
            [run_id, subagent_run_id, name, *_entity_key(name, content, event)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        message_id = "moldy-activity-" + hashlib.sha256(key.encode()).hexdigest()
        if self._snapshots.get(message_id) == content:
            return []
        self._snapshots[message_id] = content
        return [
            ActivitySnapshotEvent(
                message_id=message_id,
                activity_type=f"moldy.{name}",
                content=content,
                subagent_run_id=subagent_run_id,
            )
        ]
