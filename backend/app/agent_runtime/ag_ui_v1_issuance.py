"""Public identity of one tool issuance, including its authorized continuations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_runtime.protocol_events import StoredProtocolEvent


def public_tool_call_id(
    run_id: str, owner: str | None, parent_message_id: str | None, raw_id: str
) -> str:
    """Qualify a provider-local tool ID by the run, child, and message that issued it."""
    key = json.dumps(
        [run_id, owner, parent_message_id, raw_id], ensure_ascii=False, separators=(",", ":")
    )
    return "tool-" + hashlib.sha256(key.encode()).hexdigest()


def public_tool_result_id(tool_call_id: str) -> str:
    """A single result belongs to the same issuance before and after an approval."""
    return "tool-result-" + hashlib.sha256(tool_call_id.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolContinuation:
    """Identity proved by the selected predecessor's unresolved, authorized call."""

    owner: str | None
    raw_id: str
    name: str
    public_id: str
    parent_message_id: str | None
    source_message_id: str | None = None
    private_result: bool = False


class ToolSnapshotSources:
    """Verify which assistant produced a values-derived tool event, using only ID references."""

    def __init__(self) -> None:
        self._snapshots: dict[str | None, tuple[str, list[tuple[str, str | None]]]] = {}

    def observe_values(self, event: StoredProtocolEvent, owner: str | None) -> None:
        data = event["data"]
        rows = data.get("messages") if isinstance(data, Mapping) else None
        if not isinstance(rows, list):
            self._snapshots.pop(owner, None)
            return
        entries: list[tuple[str, str | None]] = []
        assistant = None
        for row in rows:
            kind = row.get("type") if isinstance(row, Mapping) else None
            if kind in ("ai", "assistant", "AIMessage", "AIMessageChunk"):
                raw_id = row.get("id")
                assistant = raw_id if isinstance(raw_id, str) and raw_id else None
                entries.append(("tool-started", assistant))
            elif kind in ("tool", "ToolMessage"):
                entries.append(("tool-finished", assistant))
            else:
                assistant = None
                entries.append(("", None))
        self._snapshots[owner] = (event["id"], entries)

    def synthesized_source(
        self, event: StoredProtocolEvent, owner: str | None
    ) -> tuple[bool, str | None]:
        data = event["data"]
        if not isinstance(data, Mapping):
            return False, None
        kind, raw_id = data.get("event"), data.get("tool_call_id")
        upstream = event.get("upstream_event_id")
        if (
            kind not in ("tool-started", "tool-finished")
            or not isinstance(raw_id, str)
            or not isinstance(upstream, str)
        ):
            return False, None
        suffix = f":{kind}:{raw_id}"
        if not upstream.endswith(suffix):
            return False, None
        source = upstream.removesuffix(suffix)
        prefix = f"{source}:{kind}:"
        if not event["id"].startswith(prefix) or not event["id"].endswith(f":{raw_id}"):
            return True, None
        index = event["id"][len(prefix) : -(len(raw_id) + 1)]
        snapshot = self._snapshots.get(owner)
        if (
            not snapshot
            or snapshot[0] != source
            or not index.isascii()
            or not index.isdigit()
            or len(index) > 15
        ):
            return True, None
        position = int(index)
        rows = snapshot[1]
        if position >= len(rows) or rows[position][0] != kind:
            return True, None
        return True, rows[position][1]
