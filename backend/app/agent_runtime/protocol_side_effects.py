from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.agent_runtime import event_names
from app.agent_runtime.memory_event_projection import memory_event_from_tool_result
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_custom_protocol_event
from app.agent_runtime.skill_validation_projection import (
    skill_validation_event_from_tool_result,
)
from app.agent_runtime.ui_data_projection import ui_data_from_tool_result
from app.config import settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent_runtime.streaming import ArtifactEventRecorder


def _nonempty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _tool_result(event: StoredProtocolEvent) -> tuple[str, str | None, str | None] | None:
    if event["method"] != "tools" or not isinstance(event["data"], Mapping):
        return None

    data = event["data"]
    event_name = _nonempty_str(data.get("event"))
    status = _nonempty_str(data.get("status"))
    if event_name not in {"tool-finished", "tool-error"} and status not in {
        "complete",
        "completed",
        "error",
    }:
        return None

    tool_name = (
        _nonempty_str(data.get("tool_name"))
        or _nonempty_str(data.get("name"))
        or _nonempty_str(data.get("tool"))
    )
    if tool_name is None:
        return None
    tool_call_id = _nonempty_str(data.get("tool_call_id")) or _nonempty_str(data.get("id"))
    output = (
        _nonempty_str(data.get("output"))
        or _nonempty_str(data.get("result"))
        or _nonempty_str(data.get("content"))
    )
    return tool_name, tool_call_id, output


def _artifact_protocol_event(
    source_event: StoredProtocolEvent,
    *,
    payload: dict[str, Any],
    seq: int,
    index: int,
) -> StoredProtocolEvent:
    event_id = f"{source_event['id']}:artifact:{index}"
    return stored_custom_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
        name="file_event",
        payload=payload,
        namespace=source_event["namespace"],
        event_id=event_id,
        id=event_id,
        timestamp=source_event["timestamp"],
    )


def _memory_protocol_event(
    source_event: StoredProtocolEvent,
    *,
    event_name: str,
    payload: dict[str, Any],
    seq: int,
) -> StoredProtocolEvent:
    event_id = f"{source_event['id']}:memory:{event_name}"
    return stored_custom_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
        name=event_name,
        payload=payload,
        namespace=source_event["namespace"],
        event_id=event_id,
        id=event_id,
        timestamp=source_event["timestamp"],
    )


def _ui_data_protocol_event(
    source_event: StoredProtocolEvent,
    *,
    payload: dict[str, Any],
    seq: int,
    index: int,
) -> StoredProtocolEvent:
    # Custom name ``ui_data`` (no ``moldy.`` prefix — the frontend normalizes
    # both forms), matching the artifact/memory side-effect convention here.
    event_id = f"{source_event['id']}:ui_data:{index}"
    return stored_custom_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
        name="ui_data",
        payload=payload,
        namespace=source_event["namespace"],
        event_id=event_id,
        id=event_id,
        timestamp=source_event["timestamp"],
    )


async def prepare_artifact_recorder(
    artifact_recorder: ArtifactEventRecorder | None,
    *,
    run_id: str,
) -> ArtifactEventRecorder | None:
    if artifact_recorder is None:
        return None
    try:
        await artifact_recorder.prepare()
    except Exception:  # noqa: BLE001 - optional artifact indexing must not fail streaming.
        logger.exception("artifact recorder prepare failed (run_id=%s)", run_id)
        return None
    return artifact_recorder


async def _collect_artifact_events(
    source_event: StoredProtocolEvent,
    *,
    tool_name: str,
    tool_call_id: str | None,
    artifact_recorder: ArtifactEventRecorder,
    next_seq: int,
) -> tuple[list[StoredProtocolEvent], int]:
    try:
        payloads = await artifact_recorder.collect_after_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
    except Exception:  # noqa: BLE001 - optional artifact indexing must not fail streaming.
        logger.exception(
            "artifact recorder collect failed (run_id=%s, tool=%s)",
            source_event["run_id"],
            tool_name,
        )
        return [], next_seq

    emitted: list[StoredProtocolEvent] = []
    seq = next_seq
    for index, payload in enumerate(payloads):
        seq += 1
        emitted.append(
            _artifact_protocol_event(
                source_event,
                payload=dict(payload),
                seq=seq,
                index=index,
            )
        )
    return emitted, seq


def _collect_ui_data_events(
    source_event: StoredProtocolEvent,
    *,
    tool_name: str,
    tool_call_id: str | None,
    output: str | None,
    next_seq: int,
) -> tuple[list[StoredProtocolEvent], int]:
    if output is None:
        return [], next_seq

    payloads = ui_data_from_tool_result(
        tool_name,
        output,
        tool_call_id=tool_call_id,
        demo_enabled=settings.e2e_scripted_model_enabled,
    )
    emitted: list[StoredProtocolEvent] = []
    seq = next_seq
    for index, payload in enumerate(payloads):
        # ``run_id`` keys the frontend store/attach to this run's assistant
        # message (same rule as FILE_EVENT's assistant_msg_id || run_id).
        payload["run_id"] = source_event["run_id"]
        seq += 1
        emitted.append(_ui_data_protocol_event(source_event, payload=payload, seq=seq, index=index))
    return emitted, seq


def _skill_validation_protocol_event(
    source_event: StoredProtocolEvent,
    *,
    payload: dict[str, Any],
    seq: int,
) -> StoredProtocolEvent:
    event_id = f"{source_event['id']}:skill_validation"
    return stored_custom_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
        name=event_names.SKILL_VALIDATION,
        payload=payload,
        namespace=source_event["namespace"],
        event_id=event_id,
        id=event_id,
        timestamp=source_event["timestamp"],
    )


def _collect_skill_validation_event(
    source_event: StoredProtocolEvent,
    *,
    tool_name: str,
    output: str | None,
    next_seq: int,
) -> tuple[StoredProtocolEvent | None, int]:
    if output is None:
        return None, next_seq
    payload = skill_validation_event_from_tool_result(tool_name, output)
    if payload is None:
        return None, next_seq
    seq = next_seq + 1
    return (
        _skill_validation_protocol_event(source_event, payload=payload, seq=seq),
        seq,
    )


def _collect_memory_event(
    source_event: StoredProtocolEvent,
    *,
    tool_name: str,
    output: str | None,
    next_seq: int,
) -> tuple[StoredProtocolEvent | None, int]:
    if output is None:
        return None, next_seq

    memory_event = memory_event_from_tool_result(tool_name, output)
    if memory_event is None:
        return None, next_seq

    event_name, payload = memory_event
    seq = next_seq + 1
    return (
        _memory_protocol_event(
            source_event,
            event_name=event_name,
            payload=payload,
            seq=seq,
        ),
        seq,
    )


async def collect_protocol_side_effect_events(
    event: StoredProtocolEvent,
    *,
    artifact_recorder: ArtifactEventRecorder | None,
    next_seq: int,
) -> tuple[list[StoredProtocolEvent], int]:
    tool_result = _tool_result(event)
    if tool_result is None:
        return [], next_seq

    tool_name, tool_call_id, output = tool_result
    seq = max(next_seq, event["seq"])
    emitted: list[StoredProtocolEvent] = []

    if artifact_recorder is not None:
        artifact_events, seq = await _collect_artifact_events(
            event,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            artifact_recorder=artifact_recorder,
            next_seq=seq,
        )
        emitted.extend(artifact_events)

    memory_event, seq = _collect_memory_event(
        event,
        tool_name=tool_name,
        output=output,
        next_seq=seq,
    )
    if memory_event is not None:
        emitted.append(memory_event)

    ui_data_events, seq = _collect_ui_data_events(
        event,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        output=output,
        next_seq=seq,
    )
    emitted.extend(ui_data_events)

    skill_validation_event, seq = _collect_skill_validation_event(
        event,
        tool_name=tool_name,
        output=output,
        next_seq=seq,
    )
    if skill_validation_event is not None:
        emitted.append(skill_validation_event)

    return emitted, seq
