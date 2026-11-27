"""Assemble public assistant text across model chunks and completed block snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ag_ui.core import (
    BaseEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)

from app.agent_runtime.message_utils import content_to_text
from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.marketplace.redaction import replace_prepared_secret_values


def message_id(data: Mapping[str, Any]) -> str | None:
    value = data.get("message_id") or data.get("id")
    return value if isinstance(value, str) and value else None


def _stream_safe_text(text: str, secrets: tuple[str, ...]) -> tuple[str, str]:
    """Keep only a possible secret prefix across fragments, releasing ordinary text immediately."""
    if not secrets:
        return text, ""
    safe = replace_prepared_secret_values(text, secrets, placeholder=REDACTED_SENSITIVE_FIELD)
    boundary = len(safe)
    for secret in secrets:
        start = max(0, len(safe) - len(secret) + 1)
        index = safe.find(secret[0], start, boundary)
        while index != -1:
            if secret.startswith(safe[index:]):
                boundary = index
                break
            index = safe.find(secret[0], index + 1, boundary)
    return safe[:boundary], safe[boundary:]


@dataclass(slots=True)
class _Message:
    public_id: str
    # A value is visible only once an assistant emitted text or a visible tool call.
    visible: bool = False
    observed: bool = False
    opened: bool = False
    received: list[str] = field(default_factory=list)
    blocks: dict[int | str, list[str]] = field(default_factory=dict)
    pending: str = ""


class PublicTextMessages:
    def __init__(self, run_id: str, secret_values: tuple[str, ...]) -> None:
        self.run_id = run_id
        self.secrets = secret_values
        self.messages: dict[tuple[str | None, str], _Message] = {}
        self.current: dict[str | None, str] = {}
        self._active: dict[str | None, str] = {}

    def select(
        self, owner: str | None, raw_id: str | None, event_id: str, *, starts: bool
    ) -> _Message:
        if raw_id is None and not starts:
            raw_id = self._active.get(owner)
        if raw_id is None:
            raw_id = str(
                uuid5(NAMESPACE_URL, json.dumps([self.run_id, owner, event_id, "message"]))
            )
        self.current[owner] = raw_id
        self._active[owner] = raw_id
        key = (owner, raw_id)
        if key not in self.messages:
            public_id = raw_id
            if owner is not None:
                public_id = str(
                    uuid5(NAMESPACE_URL, json.dumps([self.run_id, owner, raw_id, "message"]))
                )
            self.messages[key] = _Message(public_id)
        return self.messages[key]

    def known(self, owner: str | None, raw_id: str | None) -> str | None:
        message = self.messages.get((owner, raw_id)) if raw_id is not None else None
        return message.public_id if message and message.visible else None

    def observed(self, owner: str | None, raw_id: str | None) -> str | None:
        message = self.messages.get((owner, raw_id)) if raw_id is not None else None
        return message.public_id if message and message.observed else None

    def clear_current(self, owner: str | None) -> None:
        self._active.pop(owner, None)
        self.current.pop(owner, None)

    def _delta(self, msg: _Message, owner: str | None, text: str) -> list[BaseEvent]:
        if not text:
            return []
        msg.visible = True
        events: list[BaseEvent] = []
        if not msg.opened:
            events.append(
                TextMessageStartEvent(
                    message_id=msg.public_id, role="assistant", subagent_run_id=owner
                )
            )
            msg.opened = True
        events.append(
            TextMessageContentEvent(message_id=msg.public_id, delta=text, subagent_run_id=owner)
        )
        return events

    def project(self, data: Mapping[str, Any], owner: str | None, msg: _Message) -> list[BaseEvent]:
        event = data.get("event")
        if event == "message-finish":
            return self.finish(msg, owner)
        if event in {"content-block-start", "content-block-delta", "content-block-finish"}:
            index = data.get("index", 0)
            if not isinstance(index, (int, str)):
                return []
            if event == "content-block-delta":
                delta = data.get("delta")
                if not isinstance(delta, Mapping) or delta.get("type") != "text-delta":
                    return []
                fragment = delta.get("text")
                if not isinstance(fragment, str):
                    return []
            else:
                block = data.get("content", data.get("content_block"))
                if not isinstance(block, Mapping) or block.get("type") != "text":
                    return []
                text = block.get("text")
                if not isinstance(text, str):
                    return []
                previous = "".join(msg.blocks.get(index, ()))
                fragment = text[len(previous) :] if text.startswith(previous) else ""
            if fragment:
                msg.blocks.setdefault(index, []).append(fragment)
        elif event is not None:
            return []
        else:
            content = data.get("content", data.get("chunk", data.get("text")))
            fragment = content_to_text(content) if isinstance(content, (str, list, dict)) else ""
            kind = str(data.get("type") or data.get("role") or "").lower()
            if kind in {"ai", "assistant", "aimessage"}:
                previous = "".join(msg.received)
                fragment = fragment[len(previous) :] if fragment.startswith(previous) else ""
        if not fragment:
            return []
        msg.received.append(fragment)
        safe, msg.pending = _stream_safe_text(msg.pending + fragment, self.secrets)
        return self._delta(msg, owner, safe)

    def finish(self, msg: _Message, owner: str | None) -> list[BaseEvent]:
        active_id = self._active.get(owner)
        if active_id is not None and self.messages.get((owner, active_id)) is msg:
            self._active.pop(owner)
        events = self._delta(msg, owner, msg.pending)
        msg.pending = ""
        if msg.opened:
            events.append(TextMessageEndEvent(message_id=msg.public_id, subagent_run_id=owner))
            msg.opened = False
        return events

    def close(self, owner: str | None) -> list[BaseEvent]:
        return [
            event
            for (message_owner, _), message in self.messages.items()
            if message_owner == owner
            for event in self.finish(message, owner)
        ]
