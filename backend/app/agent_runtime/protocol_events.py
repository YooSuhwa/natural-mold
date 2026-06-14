from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, NotRequired, TypedDict
from urllib.parse import quote

MAX_MESSAGE_EVENT_ID_LENGTH: Final = 80


class StoredProtocolEvent(TypedDict):
    id: str
    upstream_event_id: str | None
    seq: int
    method: str
    namespace: list[str]
    data: Any
    run_id: str
    thread_id: str
    timestamp: str | None
    checkpoint_id: str | None
    checkpoint_ns: str | None


class ProtocolWireEvent(TypedDict):
    type: str
    method: str
    params: dict[str, Any]
    seq: int
    event_id: NotRequired[str]


class AssistantUIProjection(TypedDict):
    event: str
    data: Any


class SubscribeParams(TypedDict, total=False):
    channels: list[str] | None
    namespaces: list[list[str]] | None
    depth: int | None
    since: int | str | None


class ProtocolInterrupt(TypedDict):
    id: str
    value: Any
    ns: list[str]


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError("protocol event data must be JSON serializable") from exc
    return value


def _event_id(run_id: str, seq: int) -> str:
    return f"{run_id}:protocol:{seq:08d}"


def protocol_event_cursor(event: StoredProtocolEvent) -> str:
    return event["upstream_event_id"] or str(event["seq"])


def _canonical_input_requested_event_id(run_id: str, seq: int, index: int) -> str:
    event_id = f"{run_id}:input:{seq:08d}:{index}"
    if len(event_id) <= MAX_MESSAGE_EVENT_ID_LENGTH:
        return event_id

    run_fingerprint = hashlib.blake2s(run_id.encode("utf-8"), digest_size=8).hexdigest()
    return f"{run_fingerprint}:input:{seq:08d}:{index}"


def stored_protocol_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    method: str,
    data: Any,
    namespace: list[str] | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    id: str | None = None,
    checkpoint_id: str | None = None,
    checkpoint_ns: str | None = None,
) -> StoredProtocolEvent:
    if seq < 0:
        raise ValueError("seq must be non-negative")
    if not method:
        raise ValueError("method is required")

    ns = list(namespace or [])
    if any(not isinstance(segment, str) for segment in ns):
        raise TypeError("namespace segments must be strings")

    return {
        "id": id or event_id or _event_id(run_id, seq),
        "upstream_event_id": event_id,
        "seq": seq,
        "method": method,
        "namespace": ns,
        "data": _jsonable(data),
        "run_id": run_id,
        "thread_id": thread_id,
        "timestamp": timestamp,
        "checkpoint_id": checkpoint_id,
        "checkpoint_ns": checkpoint_ns,
    }


def stored_custom_protocol_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    name: str,
    payload: Any,
    namespace: list[str] | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    id: str | None = None,
    checkpoint_id: str | None = None,
    checkpoint_ns: str | None = None,
) -> StoredProtocolEvent:
    custom_name = name.removeprefix("custom:")
    if not custom_name:
        raise ValueError("custom event name is required")
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method="custom",
        namespace=namespace,
        event_id=event_id,
        timestamp=timestamp,
        id=id,
        checkpoint_id=checkpoint_id,
        checkpoint_ns=checkpoint_ns,
        data={"name": custom_name, "payload": payload},
    )


def resequence_protocol_event(event: StoredProtocolEvent, *, seq: int) -> StoredProtocolEvent:
    if seq == event["seq"]:
        return event

    previous_auto_id = _event_id(event["run_id"], event["seq"])
    explicit_id = (
        None
        if event["upstream_event_id"] is None and event["id"] == previous_auto_id
        else event["id"]
    )
    return stored_protocol_event(
        run_id=event["run_id"],
        thread_id=event["thread_id"],
        seq=seq,
        method=event["method"],
        namespace=event["namespace"],
        data=event["data"],
        event_id=event["upstream_event_id"],
        timestamp=event["timestamp"],
        id=explicit_id,
        checkpoint_id=event["checkpoint_id"],
        checkpoint_ns=event["checkpoint_ns"],
    )


def resequence_protocol_events(
    events: Sequence[StoredProtocolEvent],
    *,
    first_seq: int = 1,
) -> list[StoredProtocolEvent]:
    return [
        resequence_protocol_event(event, seq=seq)
        for seq, event in enumerate(events, start=first_seq)
    ]


def to_protocol_wire_event(event: StoredProtocolEvent) -> ProtocolWireEvent:
    params: dict[str, Any] = {
        "namespace": list(event["namespace"]),
        "data": event["data"],
    }
    if event["timestamp"] is not None:
        params["timestamp"] = event["timestamp"]
    if event["checkpoint_id"] is not None:
        params["checkpoint_id"] = event["checkpoint_id"]
    if event["checkpoint_ns"] is not None:
        params["checkpoint_ns"] = event["checkpoint_ns"]

    wire: ProtocolWireEvent = {
        "type": "event",
        "method": event["method"],
        "params": params,
        "seq": event["seq"],
        "event_id": event["upstream_event_id"] or event["id"],
    }
    return wire


def to_assistant_ui_projection(event: StoredProtocolEvent) -> AssistantUIProjection:
    namespace = event["namespace"]
    name = event["method"]
    if namespace:
        encoded = "|".join(quote(segment, safe="") for segment in namespace)
        name = f"{name}|{encoded}"
    return {"event": name, "data": event["data"]}


def format_protocol_sse(event: StoredProtocolEvent) -> str:
    cursor = protocol_event_cursor(event)
    payload = json.dumps(to_protocol_wire_event(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {cursor}\nevent: message\ndata: {payload}\n\n"


def protocol_interrupts_from_event(event: StoredProtocolEvent) -> list[ProtocolInterrupt]:
    data = event["data"]
    if not isinstance(data, Mapping):
        return []
    if event["method"] == "input.requested":
        interrupt = _input_requested_interrupt(data, fallback_namespace=event["namespace"])
        return [interrupt] if interrupt is not None else []
    raw_interrupts = _raw_interrupts_for_method(event["method"], data)
    return _interrupts_from_raw(raw_interrupts, fallback_namespace=event["namespace"])


def canonical_input_requested_events(
    event: StoredProtocolEvent,
    *,
    first_seq: int | None = None,
) -> list[StoredProtocolEvent]:
    if event["method"] == "input.requested":
        return []

    events: list[StoredProtocolEvent] = []
    base_seq = event["seq"] if first_seq is None else first_seq
    for index, interrupt in enumerate(protocol_interrupts_from_event(event)):
        seq = base_seq + index
        event_id = _canonical_input_requested_event_id(event["run_id"], seq, index)
        events.append(
            stored_protocol_event(
                run_id=event["run_id"],
                thread_id=event["thread_id"],
                seq=seq,
                event_id=event_id,
                method="input.requested",
                namespace=interrupt["ns"],
                data={
                    "interrupt_id": interrupt["id"],
                    "payload": interrupt["value"],
                    "namespace": interrupt["ns"],
                },
                timestamp=event["timestamp"],
                checkpoint_id=event["checkpoint_id"],
                checkpoint_ns=event["checkpoint_ns"],
            )
        )
    return events


def matches_subscription(event: StoredProtocolEvent, params: SubscribeParams | None) -> bool:
    if not params:
        return True
    if not _matches_since(event, params.get("since")):
        return False
    if not _matches_channels(event, params.get("channels")):
        return False
    return _matches_namespaces(event, params.get("namespaces"), params.get("depth"))


def _matches_since(event: StoredProtocolEvent, since: int | str | None) -> bool:
    if since is None:
        return True
    if isinstance(since, int):
        return event["seq"] > since
    if since.isdigit():
        return event["seq"] > int(since)
    return since not in {event["id"], event["upstream_event_id"]}


def _custom_channel(event: StoredProtocolEvent) -> str | None:
    method = event["method"]
    if method.startswith("custom:"):
        return method
    if method != "custom" or not isinstance(event["data"], dict):
        return None

    name = event["data"].get("name") or event["data"].get("channel")
    if not isinstance(name, str) or not name:
        return None
    return name if name.startswith("custom:") else f"custom:{name}"


def _matches_channels(event: StoredProtocolEvent, channels: list[str] | None) -> bool:
    if not channels:
        return True

    method = event["method"]
    custom = _custom_channel(event)
    for channel in channels:
        if channel == method:
            return True
        if channel == "input" and method.startswith("input."):
            return True
        if channel == "custom" and (method == "custom" or custom is not None):
            return True
        if custom is not None and channel == custom:
            return True
    return False


def _input_requested_interrupt(
    data: Mapping[str, Any],
    *,
    fallback_namespace: list[str],
) -> ProtocolInterrupt | None:
    interrupt_id = _string_value(data.get("interrupt_id")) or _string_value(data.get("id"))
    if interrupt_id is None:
        return None
    value = data.get("payload") if "payload" in data else data.get("value")
    return {
        "id": interrupt_id,
        "value": value,
        "ns": _namespace(data.get("namespace") or data.get("ns"), fallback_namespace),
    }


def _raw_interrupts_for_method(method: str, data: Mapping[str, Any]) -> Any:
    if method in {"values", "updates"}:
        return data.get("__interrupt__")
    if method == "tasks":
        return data.get("interrupts")
    return None


def _interrupts_from_raw(
    raw_interrupts: Any,
    *,
    fallback_namespace: list[str],
) -> list[ProtocolInterrupt]:
    if not isinstance(raw_interrupts, Sequence) or isinstance(raw_interrupts, str | bytes):
        return []

    interrupts: list[ProtocolInterrupt] = []
    for raw in raw_interrupts:
        if not isinstance(raw, Mapping):
            continue
        interrupt_id = _string_value(raw.get("id")) or _string_value(raw.get("interrupt_id"))
        if interrupt_id is None:
            continue
        value = raw.get("value") if "value" in raw else raw.get("payload")
        interrupts.append(
            {
                "id": interrupt_id,
                "value": value,
                "ns": _namespace(raw.get("ns") or raw.get("namespace"), fallback_namespace),
            }
        )
    return interrupts


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _namespace(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [segment for segment in value if isinstance(segment, str)]
    return list(fallback)


def _matches_namespaces(
    event: StoredProtocolEvent,
    namespaces: list[list[str]] | None,
    depth: int | None,
) -> bool:
    if not namespaces:
        return True

    event_namespace = event["namespace"]
    return any(_namespace_matches(event_namespace, target, depth) for target in namespaces)


def _namespace_matches(event_namespace: list[str], target: list[str], depth: int | None) -> bool:
    if event_namespace[: len(target)] != target:
        return False
    if depth is None:
        return True
    return len(event_namespace) - len(target) <= depth
