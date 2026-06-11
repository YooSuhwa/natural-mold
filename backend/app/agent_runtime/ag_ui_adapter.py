from __future__ import annotations

import json
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


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _tool_call_id(data: Mapping[str, Any], source_id: str) -> str:
    return _str_or_none(data.get("tool_call_id")) or f"{source_id}:tool"


def _tool_name(data: Mapping[str, Any]) -> str:
    return _str_or_none(data.get("tool_name")) or "unknown_tool"


def _custom_event(
    name: str,
    data: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "type": "CUSTOM",
        "name": name,
        "value": {
            "threadId": thread_id,
            "runId": run_id,
            "payload": dict(data),
        },
    }


def moldy_event_to_ag_ui_events(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Convert one Moldy SSE event into one or more AG-UI core events.

    The adapter intentionally keeps Moldy's richer payload in ``rawEvent`` for
    lossy protocol edges such as token usage and already-complete tool args.
    Native AG-UI clients can use the standard fields, while Moldy's current UI
    can reconstruct the original event stream without another backend fork.
    """

    source_id = _str_or_none(evt.get("id")) or f"{run_id}:event"
    event_name = _str_or_none(evt.get("event"))
    data = _as_dict(evt.get("data"))
    if event_name == event_names.MESSAGE_START:
        started: dict[str, Any] = {
            "type": "RUN_STARTED",
            "threadId": thread_id,
            "runId": run_id,
        }
        if "input" in data:
            started["input"] = data["input"]
        return [
            started,
            {
                # TEXT_MESSAGE_{START,CONTENT,END} 는 반드시 같은 messageId 를
                # 공유해야 표준 AG-UI client 가 메시지를 매칭한다. CONTENT/END
                # 이벤트는 원본 data 에 메시지 id 가 없어 run_id 만이 안정적인
                # 공통 키다 (원본 id 는 rawEvent 로 보존).
                "type": "TEXT_MESSAGE_START",
                "messageId": run_id,
                "role": "assistant",
                "rawEvent": data,
            },
        ]

    if event_name == event_names.CONTENT_DELTA:
        delta = data.get("delta", data.get("content", ""))
        return [
            {
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": run_id,
                "delta": str(delta) if delta is not None else "",
                "rawEvent": data,
            }
        ]

    if event_name == event_names.MESSAGE_END:
        status = _str_or_none(data.get("status")) or "completed"
        events: list[dict[str, Any]] = [
            {
                "type": "TEXT_MESSAGE_END",
                "messageId": run_id,
                "rawEvent": data,
            }
        ]
        if status == "failed":
            events.append(
                {
                    "type": "RUN_ERROR",
                    "message": _str_or_none(data.get("message")) or "Run failed.",
                    "code": "moldy_run_failed",
                    "rawEvent": data,
                }
            )
        else:
            events.append(
                {
                    "type": "RUN_FINISHED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "result": {"status": status},
                    "rawEvent": data,
                }
            )
        return events

    if event_name == event_names.ERROR:
        error_event: dict[str, Any] = {
            "type": "RUN_ERROR",
            "message": _str_or_none(data.get("message")) or "Run failed.",
            "rawEvent": data,
        }
        # AG-UI 스키마에서 code 는 optional — None 값 대신 키 자체를 생략해
        # 표준 client 호환을 지킨다 (message_end failed 분기와 동일한 규칙).
        code = _str_or_none(data.get("code"))
        if code:
            error_event["code"] = code
        return [error_event]

    if event_name == event_names.TOOL_CALL_START:
        tool_call_id = _tool_call_id(data, source_id)
        parameters = data.get("parameters")
        args_json = json.dumps(
            parameters if isinstance(parameters, Mapping) else {},
            ensure_ascii=False,
        )
        return [
            {
                "type": "TOOL_CALL_START",
                "toolCallId": tool_call_id,
                "toolCallName": _tool_name(data),
                "parentMessageId": run_id,
                "rawEvent": data,
            },
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": tool_call_id,
                "delta": args_json,
                "rawEvent": data,
            },
            {
                "type": "TOOL_CALL_END",
                "toolCallId": tool_call_id,
                "rawEvent": data,
            },
        ]

    if event_name == event_names.TOOL_CALL_RESULT:
        tool_call_id = _tool_call_id(data, source_id)
        result = data.get("result", "")
        return [
            {
                "type": "TOOL_CALL_RESULT",
                "messageId": f"{tool_call_id}:result",
                "toolCallId": tool_call_id,
                "content": str(result) if result is not None else "",
                "role": "tool",
                "rawEvent": data,
            }
        ]

    custom_names = {
        event_names.FILE_EVENT: "moldy.file_event",
        event_names.MEMORY_PROPOSED: "moldy.memory_proposed",
        event_names.MEMORY_SAVED: "moldy.memory_saved",
        event_names.MEMORY_REJECTED: "moldy.memory_rejected",
        event_names.MEMORY_DELETED: "moldy.memory_deleted",
        event_names.INTERRUPT: "moldy.interrupt",
        event_names.STALE: "moldy.stale",
    }
    if event_name in custom_names:
        return [_custom_event(custom_names[event_name], data, thread_id=thread_id, run_id=run_id)]

    return [_custom_event("moldy.raw_event", data, thread_id=thread_id, run_id=run_id)]


def brokered_moldy_event_to_ag_ui_events(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> list[AgUiBrokeredEvent]:
    source_id = _str_or_none(evt.get("id")) or f"{run_id}:event"
    converted = moldy_event_to_ag_ui_events(evt, thread_id=thread_id, run_id=run_id)
    results: list[AgUiBrokeredEvent] = []
    for index, data in enumerate(converted):
        event_type = data.get("type")
        if not isinstance(event_type, str):
            continue
        results.append(
            {
                "id": f"{source_id}{AG_UI_ID_SUFFIX}{index}",
                "event": event_type,  # type: ignore[typeddict-item]
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
