from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.agent_runtime import event_names
from app.agent_runtime.langgraph_compaction_start import is_internal_summary
from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    resequence_protocol_event,
    stored_custom_protocol_event,
)
from app.agent_runtime.protocol_persistence import persistable_wire_protocol_event
from app.agent_runtime.protocol_redaction import redact_protocol_data


@dataclass(frozen=True, slots=True)
class StableEventProjection:
    """The public live view and its stricter append-only persistence view."""

    wire_event: StoredProtocolEvent
    persistable_event: dict[str, Any]


def project_stable_event(event: StoredProtocolEvent, *, next_seq: int) -> StableEventProjection:
    """Project one normalized record to stable live and persisted contracts."""

    sequenced = resequence_protocol_event(event, seq=next_seq) if event["seq"] < next_seq else event
    projected_data = project_offload_egress_data(sequenced["data"])
    wire_event: StoredProtocolEvent = {
        **sequenced,
        "data": redact_protocol_data(
            sequenced["method"],
            projected_data,
            redact_memory=False,
        ),
    }
    return StableEventProjection(
        wire_event=wire_event,
        persistable_event=persistable_wire_protocol_event(wire_event),
    )


def is_empty_input_requested_event(event: StoredProtocolEvent) -> bool:
    from app.agent_runtime.protocol_events import protocol_interrupts_from_event

    return event["method"] == "input.requested" and not protocol_interrupts_from_event(event)


def _compaction_summarization_event(event: StoredProtocolEvent) -> Mapping[str, Any] | None:
    if event["method"] != "values" or not isinstance(event["data"], Mapping):
        return None
    value = event["data"].get("_summarization_event")
    return value if isinstance(value, Mapping) else None


def is_summary_token_event(event: StoredProtocolEvent) -> bool:
    data = event["data"]
    return (
        event["method"] == "messages"
        and isinstance(data, Mapping)
        and is_internal_summary(data.get("metadata"))
    )


def compaction_history_id(event: StoredProtocolEvent) -> str | None:
    summarization = _compaction_summarization_event(event)
    if summarization is None:
        return None
    projected = project_offload_egress_data({"_summarization_event": summarization})
    event_data = projected.get("_summarization_event") if isinstance(projected, Mapping) else None
    history_id = event_data.get("history_id") if isinstance(event_data, Mapping) else None
    return history_id if isinstance(history_id, str) else None


def compaction_cutoff(event: StoredProtocolEvent) -> tuple[int, str | None] | None:
    summarization = _compaction_summarization_event(event)
    if summarization is None:
        return None
    index = summarization.get("cutoff_index")
    if not isinstance(index, int) or index <= 0:
        return None
    # Newer checkpoints retain the message ID across deletions before the cutoff.
    message_id = summarization.get("cutoff_message_id")
    return index, message_id if isinstance(message_id, str) else None


def compaction_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    state: str,
    history_id: str | None = None,
    cutoff_index: int | None = None,
) -> StoredProtocolEvent:
    payload: dict[str, Any] = {"state": state}
    if history_id is not None:
        payload["history_id"] = history_id
    if cutoff_index is not None:
        payload["cutoff_index"] = cutoff_index
    stable_id = f"{run_id}:compaction:{state}"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=event_names.COMPACTION,
        payload=payload,
        event_id=stable_id,
        id=stable_id,
    )


def head_custom_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    name: str,
    payload: dict[str, Any],
    stable_suffix: str,
) -> StoredProtocolEvent:
    stable_id = f"{run_id}:{stable_suffix}"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=name,
        payload=payload,
        event_id=stable_id,
        id=stable_id,
    )


def annotate_session_consent_eligibility(
    input_event: StoredProtocolEvent,
    consent_tools: list[str],
) -> None:
    data = input_event.get("data")
    if not isinstance(data, dict):
        return
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return
    review_configs = payload.get("review_configs")
    if not isinstance(review_configs, list):
        return
    eligible = set(consent_tools)
    for config in review_configs:
        if isinstance(config, dict) and config.get("action_name") in eligible:
            config["session_consent_eligible"] = True
