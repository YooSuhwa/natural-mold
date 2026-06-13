from __future__ import annotations

import json
from typing import Any, NotRequired, TypedDict
from urllib.parse import quote


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


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError("protocol event data must be JSON serializable") from exc
    return value


def _event_id(run_id: str, seq: int) -> str:
    return f"{run_id}:protocol:{seq:08d}"


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
    }


def to_protocol_wire_event(event: StoredProtocolEvent) -> ProtocolWireEvent:
    params: dict[str, Any] = {
        "namespace": list(event["namespace"]),
        "data": event["data"],
    }
    if event["timestamp"] is not None:
        params["timestamp"] = event["timestamp"]

    wire: ProtocolWireEvent = {
        "type": "event",
        "method": event["method"],
        "params": params,
        "seq": event["seq"],
    }
    if event["upstream_event_id"]:
        wire["event_id"] = event["upstream_event_id"]
    return wire


def to_assistant_ui_projection(event: StoredProtocolEvent) -> AssistantUIProjection:
    namespace = event["namespace"]
    name = event["method"]
    if namespace:
        encoded = "|".join(quote(segment, safe="") for segment in namespace)
        name = f"{name}|{encoded}"
    return {"event": name, "data": event["data"]}


def format_protocol_sse(event: StoredProtocolEvent) -> str:
    cursor = event["upstream_event_id"] or str(event["seq"])
    payload = json.dumps(to_protocol_wire_event(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {cursor}\nevent: message\ndata: {payload}\n\n"


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
        if channel == "custom" and (method == "custom" or custom is not None):
            return True
        if custom is not None and channel == custom:
            return True
    return False


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
