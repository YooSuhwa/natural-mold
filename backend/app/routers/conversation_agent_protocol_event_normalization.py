from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)


def stored_compatible_protocol_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    method: str,
    namespace: list[str] | None = None,
    data: Any = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    id: str | None = None,
    checkpoint_id: str | None = None,
    checkpoint_ns: str | None = None,
) -> StoredProtocolEvent:
    if method.startswith("custom:"):
        name = method.removeprefix("custom:")
        if name:
            return stored_custom_protocol_event(
                run_id=run_id,
                thread_id=thread_id,
                seq=seq,
                name=name,
                payload=_custom_payload(data, name=name),
                namespace=namespace,
                event_id=event_id,
                timestamp=timestamp,
                id=id,
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
            )

    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method=method,
        namespace=namespace,
        data=data,
        event_id=event_id,
        timestamp=timestamp,
        id=id,
        checkpoint_id=checkpoint_id,
        checkpoint_ns=checkpoint_ns,
    )


def _custom_payload(data: Any, *, name: str) -> Any:
    if not isinstance(data, Mapping):
        return data

    raw_name = data.get("name")
    raw_channel = data.get("channel")
    custom_name = f"custom:{name}"
    if raw_name in {name, custom_name} and "payload" in data:
        return data.get("payload")
    if raw_channel in {name, custom_name} and "payload" in data:
        return data.get("payload")
    return data
