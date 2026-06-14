from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Final, Literal, TypedDict

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent, slice_events_after

AgUiEventType = Literal[
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
    "CUSTOM",
]


class AgUiBrokeredEvent(TypedDict):
    id: str
    event: AgUiEventType
    data: dict[str, Any]


AG_UI_ID_SUFFIX: Final = ":ag:"
AG_UI_PROTOCOL_HEADER: Final = "ag_ui"
_AG_UI_ID_RE: Final = re.compile(r"^(?P<source>.+):ag:(?P<index>\d+)$")


def is_ag_ui_event_id(event_id: str | None) -> bool:
    return bool(event_id and _AG_UI_ID_RE.match(event_id))


def source_event_id_from_ag_ui(event_id: str | None) -> str | None:
    if not event_id:
        return None
    match = _AG_UI_ID_RE.match(event_id)
    if match:
        return match.group("source")
    return event_id


def brokered_moldy_event_to_ag_ui_events(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> list[AgUiBrokeredEvent]:
    from app.agent_runtime.ag_ui_adapter import moldy_event_to_ag_ui_events

    source_id = _str_or_none(evt.get("id")) or f"{run_id}:event"
    converted = moldy_event_to_ag_ui_events(evt, thread_id=thread_id, run_id=run_id)
    results: list[AgUiBrokeredEvent] = []
    for index, data in enumerate(converted):
        event_type = _ag_ui_event_type(data.get("type"))
        if event_type is None:
            continue
        results.append(
            {
                "id": f"{source_id}{AG_UI_ID_SUFFIX}{index}",
                "event": event_type,
                "data": data,
            }
        )
    return results


def flatten_moldy_events_to_ag_ui(
    events: Iterable[Mapping[str, Any]],
    *,
    thread_id: str,
    run_id: str,
) -> Iterator[AgUiBrokeredEvent]:
    for evt in events:
        yield from brokered_moldy_event_to_ag_ui_events(evt, thread_id=thread_id, run_id=run_id)


def slice_ag_ui_events_after(
    events: Iterable[Mapping[str, Any]],
    after_id: str | None,
    *,
    thread_id: str,
    run_id: str,
) -> Iterator[AgUiBrokeredEvent]:
    yield from slice_events_after(
        flatten_moldy_events_to_ag_ui(events, thread_id=thread_id, run_id=run_id),
        after_id,
    )


def format_ag_ui_sse(evt: AgUiBrokeredEvent) -> str:
    from app.agent_runtime.streaming import format_sse

    return format_sse(evt["event"], evt["data"], event_id=evt["id"])


def stale_run_event(run_id: str, *, reason: str, last_event_id: str | None) -> BrokeredEvent:
    return {
        "id": f"{run_id}-stale",
        "event": event_names.STALE,
        "data": {
            "reason": reason,
            "run_id": run_id,
            "last_event_id": last_event_id,
        },
    }


def _ag_ui_event_type(value: object) -> AgUiEventType | None:
    match value:
        case (
            "RUN_STARTED"
            | "RUN_FINISHED"
            | "RUN_ERROR"
            | "TEXT_MESSAGE_START"
            | "TEXT_MESSAGE_CONTENT"
            | "TEXT_MESSAGE_END"
            | "TOOL_CALL_START"
            | "TOOL_CALL_ARGS"
            | "TOOL_CALL_END"
            | "TOOL_CALL_RESULT"
            | "CUSTOM"
        ):
            return value
        case _:
            return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
