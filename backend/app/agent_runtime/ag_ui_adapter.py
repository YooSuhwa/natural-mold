from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.agent_runtime import event_names
from app.agent_runtime.ag_ui_protocol_adapter import protocol_event_to_ag_ui_events
from app.agent_runtime.ag_ui_streaming import (
    AG_UI_ID_SUFFIX,
    AG_UI_PROTOCOL_HEADER,
    AgUiBrokeredEvent,
    AgUiEventType,
    brokered_moldy_event_to_ag_ui_events,
    flatten_moldy_events_to_ag_ui,
    format_ag_ui_sse,
    is_ag_ui_event_id,
    slice_ag_ui_events_after,
    source_event_id_from_ag_ui,
    stale_run_event,
)

__all__ = [
    "AG_UI_ID_SUFFIX",
    "AG_UI_PROTOCOL_HEADER",
    "AgUiBrokeredEvent",
    "AgUiEventType",
    "brokered_moldy_event_to_ag_ui_events",
    "flatten_moldy_events_to_ag_ui",
    "format_ag_ui_sse",
    "is_ag_ui_event_id",
    "moldy_event_to_ag_ui_events",
    "slice_ag_ui_events_after",
    "source_event_id_from_ag_ui",
    "stale_run_event",
]


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


# AG-UI is external compatibility; Moldy's primary chat runtime uses canonical LangGraph protocol.
def moldy_event_to_ag_ui_events(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    protocol_events = protocol_event_to_ag_ui_events(
        evt,
        thread_id=thread_id,
        run_id=run_id,
        legacy_converter=moldy_event_to_ag_ui_events,
    )
    if protocol_events is not None:
        return protocol_events

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
