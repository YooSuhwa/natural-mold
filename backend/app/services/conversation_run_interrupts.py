from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.agent_runtime import event_names


def interrupt_id_from_events(events: Sequence[Mapping[str, object]]) -> str | None:
    for event in reversed(events):
        if event.get("method") == "input.requested":
            data = event.get("data")
            if not isinstance(data, Mapping):
                continue
            interrupt_id = data.get("interrupt_id") or data.get("id")
            return interrupt_id if isinstance(interrupt_id, str) and interrupt_id else None
        if event.get("event") != event_names.INTERRUPT:
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        interrupt_id = data.get("interrupt_id")
        return interrupt_id if isinstance(interrupt_id, str) and interrupt_id else None
    return None


def has_interrupt_events(events: Sequence[Mapping[str, object]]) -> bool:
    for event in events:
        if event.get("method") == "input.requested":
            return True
        if event.get("event") == event_names.INTERRUPT:
            return True
    return False
