from __future__ import annotations

from typing import Any

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.services.conversation_protocol_event_projection import (
    CompatibleEventRequest,
)
from app.services.conversation_protocol_event_projection import (
    stored_compatible_protocol_event as _stored_compatible_protocol_event,
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
    return _stored_compatible_protocol_event(
        CompatibleEventRequest(
            run_id,
            thread_id,
            seq,
            method,
            namespace,
            data,
            event_id,
            timestamp,
            id,
            checkpoint_id,
            checkpoint_ns,
        )
    )
