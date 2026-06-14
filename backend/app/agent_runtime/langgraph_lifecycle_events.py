from __future__ import annotations

from typing import Final, Literal, NotRequired, TypedDict

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event

LifecycleEventName = Literal["running", "completed", "failed", "interrupted"]
TerminalLifecycleEventName = Literal["completed", "failed", "interrupted"]

TERMINAL_LIFECYCLE_EVENTS: Final[frozenset[TerminalLifecycleEventName]] = frozenset(
    {"completed", "failed", "interrupted"}
)


class LifecycleError(TypedDict):
    message: str


class LifecycleData(TypedDict):
    event: LifecycleEventName
    error: NotRequired[LifecycleError]


def lifecycle_protocol_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    event: LifecycleEventName,
    error_message: str | None = None,
) -> StoredProtocolEvent:
    data: LifecycleData = {"event": event}
    if error_message is not None:
        data["error"] = {"message": error_message}
    event_id = f"{run_id}:lifecycle:{event}"
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method="lifecycle",
        data=data,
        event_id=event_id,
        id=event_id,
    )


def terminal_lifecycle_event(has_pending_input: bool) -> TerminalLifecycleEventName:
    if has_pending_input:
        return "interrupted"
    return "completed"
