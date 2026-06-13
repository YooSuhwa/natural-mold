from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.agent_runtime import event_names
from app.agent_runtime.legacy_event_projection import project_protocol_event_to_legacy
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event


class LegacyAgUiConverter(Protocol):
    def __call__(
        self,
        evt: Mapping[str, Any],
        *,
        thread_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]: ...


def protocol_event_to_ag_ui_events(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
    legacy_converter: LegacyAgUiConverter,
) -> list[dict[str, Any]] | None:
    protocol_event = _protocol_event(evt, thread_id=thread_id, run_id=run_id)
    if protocol_event is None:
        return None
    return _project_protocol_event(
        protocol_event,
        thread_id=thread_id,
        run_id=run_id,
        legacy_converter=legacy_converter,
    )


def _project_protocol_event(
    event: StoredProtocolEvent,
    *,
    thread_id: str,
    run_id: str,
    legacy_converter: LegacyAgUiConverter,
) -> list[dict[str, Any]]:
    data = _as_dict(event["data"])
    if event["method"] == "messages" and _is_reasoning_event(data):
        projected = [_custom_event("moldy.reasoning", data, thread_id=thread_id, run_id=run_id)]
    else:
        legacy_events = _legacy_events_from_protocol(event, data)
        projected = [
            item
            for legacy in legacy_events
            for item in legacy_converter(legacy, thread_id=thread_id, run_id=run_id)
        ]

    if not projected:
        projected = [_custom_event("moldy.raw_event", data, thread_id=thread_id, run_id=run_id)]
    _attach_raw_protocol(projected, event, is_tool_error=data.get("event") == "tool-error")
    return projected


def _legacy_events_from_protocol(
    event: StoredProtocolEvent,
    data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    legacy_events = list(project_protocol_event_to_legacy(event))
    if legacy_events or event["method"] != "messages":
        return legacy_events
    text = _protocol_text_delta(data)
    if not text:
        return []
    return [
        {
            "id": event["upstream_event_id"] or event["id"],
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": text},
        }
    ]


def _protocol_event(
    evt: Mapping[str, Any],
    *,
    thread_id: str,
    run_id: str,
) -> StoredProtocolEvent | None:
    method = _str_or_none(evt.get("method"))
    if method:
        return _protocol_event_from_method(evt, method=method, thread_id=thread_id, run_id=run_id)

    payload = evt.get("data")
    if isinstance(payload, Mapping) and (payload.get("type") == "event" or "params" in payload):
        return _protocol_event(payload, thread_id=thread_id, run_id=run_id)
    return None


def _protocol_event_from_method(
    evt: Mapping[str, Any],
    *,
    method: str,
    thread_id: str,
    run_id: str,
) -> StoredProtocolEvent | None:
    params = evt.get("params")
    if isinstance(params, Mapping):
        return stored_protocol_event(
            run_id=run_id,
            thread_id=thread_id,
            seq=_int_value(evt.get("seq")),
            method=method,
            namespace=_namespace(params.get("namespace")),
            data=params.get("data"),
            event_id=_str_or_none(evt.get("event_id") or evt.get("upstream_event_id")),
            timestamp=_str_or_none(params.get("timestamp")),
            id=_str_or_none(evt.get("id")),
        )
    if "data" not in evt:
        return None
    return stored_protocol_event(
        run_id=_str_or_none(evt.get("run_id")) or run_id,
        thread_id=_str_or_none(evt.get("thread_id")) or thread_id,
        seq=_int_value(evt.get("seq")),
        method=method,
        namespace=_namespace(evt.get("namespace")),
        data=evt.get("data"),
        event_id=_str_or_none(evt.get("upstream_event_id") or evt.get("event_id")),
        timestamp=_str_or_none(evt.get("timestamp")),
        id=_str_or_none(evt.get("id")),
    )


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
        "value": {"threadId": thread_id, "runId": run_id, "payload": dict(data)},
    }


def _attach_raw_protocol(
    events: list[dict[str, Any]],
    event: StoredProtocolEvent,
    *,
    is_tool_error: bool,
) -> None:
    raw = {
        "id": event["id"],
        "upstream_event_id": event["upstream_event_id"],
        "seq": event["seq"],
        "method": event["method"],
        "namespace": list(event["namespace"]),
        "data": event["data"],
    }
    for item in events:
        item["rawEvent"] = raw
        if is_tool_error and item.get("type") == "TOOL_CALL_RESULT":
            item["isError"] = True


def _protocol_text_delta(data: Mapping[str, Any]) -> str:
    delta = data.get("delta")
    if isinstance(delta, Mapping) and isinstance(delta.get("text"), str):
        return delta["text"]
    for key in ("text", "content", "delta", "chunk"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _is_reasoning_event(data: Mapping[str, Any]) -> bool:
    delta = data.get("delta")
    if isinstance(delta, Mapping):
        kind = _str_or_none(delta.get("type")) or ""
        if "reasoning" in kind or "thinking" in kind:
            return True
    for key in ("content_block", "block", "chunk"):
        block = data.get(key)
        if isinstance(block, Mapping):
            kind = _str_or_none(block.get("type")) or ""
            if "reasoning" in kind or "thinking" in kind:
                return True
    kind = _str_or_none(data.get("type")) or _str_or_none(data.get("event")) or ""
    return "reasoning" in kind or "thinking" in kind


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else 0


def _namespace(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [segment for segment in value if isinstance(segment, str)]
    return []


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
