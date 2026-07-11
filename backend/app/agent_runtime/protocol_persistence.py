from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.protocol_redaction import redact_memory_content, redact_protocol_data

COMPACT_STATE_METHODS: Final = frozenset({"values", "updates"})
PERSISTED_STATE_KEYS: Final = frozenset(
    {"__interrupt__", "todos", "files", "artifacts", "async_tasks", "tasks"}
)


def persistable_protocol_event(event: StoredProtocolEvent) -> dict[str, Any]:
    """Persisted shape from a RAW (un-redacted) event — safe default.

    Runs the full value/key redaction itself. Callers that already hold the
    wire-redacted event (the streaming ``emit`` hot path) should use
    :func:`persistable_wire_protocol_event` instead of paying the recursive
    redaction pass a second time (BE-P5(b)).
    """

    redacted: StoredProtocolEvent = {
        **event,
        "data": redact_protocol_data(event["method"], event["data"], redact_memory=False),
    }
    return persistable_wire_protocol_event(redacted)


def persistable_wire_protocol_event(wire_event: StoredProtocolEvent) -> dict[str, Any]:
    """Persisted shape from an already wire-redacted event (BE-P5(b) hot path).

    PRECONDITION: ``wire_event["data"]`` has been through
    ``redact_protocol_data(method, data, redact_memory=False)`` — i.e. value
    masking and sensitive-key redaction are done. This only adds the two
    persist-specific deltas on top of the wire view:

    1. ``values``/``updates`` state snapshots are compacted to message refs.
    2. Memory content is masked (persisted/shared surfaces must not carry
       memory bodies; the live wire keeps them — W2-3 contract).

    Never feed a raw event here — its secrets would persist in plaintext.
    """

    payload = dict(wire_event)
    if wire_event["method"] in COMPACT_STATE_METHODS:
        payload["data"] = _compact_state_snapshot(
            wire_event["data"],
            checkpoint_id=wire_event["checkpoint_id"],
            checkpoint_ns=wire_event["checkpoint_ns"],
        )
    payload["data"] = redact_memory_content(wire_event["method"], payload["data"])
    return payload


def _compact_state_snapshot(
    value: Any,
    *,
    checkpoint_id: str | None,
    checkpoint_ns: str | None,
) -> Any:
    if isinstance(value, Mapping):
        return _compact_state_mapping(
            value,
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _compact_state_snapshot(
                item,
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
            )
            for item in value
        ]
    return value


def _compact_state_mapping(
    value: Mapping[str, Any],
    *,
    checkpoint_id: str | None,
    checkpoint_ns: str | None,
) -> dict[str, Any]:
    has_state_snapshot = "messages" in value or any(key in value for key in PERSISTED_STATE_KEYS)
    if not has_state_snapshot:
        return {
            str(key): _compact_state_snapshot(
                item,
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
            )
            for key, item in value.items()
        }

    compact: dict[str, Any] = {}
    for key in PERSISTED_STATE_KEYS:
        if key in value:
            compact[key] = _compact_state_snapshot(
                value[key],
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
            )
    if "messages" in value:
        compact["messages"] = _message_refs(
            value["messages"],
            fallback_checkpoint_id=checkpoint_id,
            fallback_checkpoint_ns=checkpoint_ns,
        )
    return compact


def _message_refs(
    messages: Any,
    *,
    fallback_checkpoint_id: str | None,
    fallback_checkpoint_ns: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes | bytearray):
        return []

    refs: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        ref = _message_ref(
            message,
            fallback_checkpoint_id=fallback_checkpoint_id,
            fallback_checkpoint_ns=fallback_checkpoint_ns,
        )
        refs.append(ref if ref else {"index": index})
    return refs


def _message_ref(
    message: Any,
    *,
    fallback_checkpoint_id: str | None,
    fallback_checkpoint_ns: str | None,
) -> dict[str, Any]:
    if not isinstance(message, Mapping):
        return {}

    ref: dict[str, Any] = {}
    message_id = _string_or_none(message.get("id"))
    message_type = _string_or_none(message.get("type") or message.get("role"))
    checkpoint_id = _string_or_none(message.get("checkpoint_id")) or fallback_checkpoint_id
    checkpoint_ns = _string_or_none(message.get("checkpoint_ns")) or fallback_checkpoint_ns

    metadata = _metadata_mapping(message)
    if metadata is not None:
        checkpoint_id = _string_or_none(metadata.get("checkpoint_id")) or checkpoint_id
        checkpoint_ns = _string_or_none(metadata.get("checkpoint_ns")) or checkpoint_ns

    if message_id is not None:
        ref["id"] = message_id
    if message_type is not None:
        ref["type"] = message_type
    if checkpoint_id is not None:
        ref["checkpoint_id"] = checkpoint_id
    if checkpoint_ns is not None:
        ref["checkpoint_ns"] = checkpoint_ns
    return ref


def _metadata_mapping(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    additional_kwargs = message.get("additional_kwargs")
    if not isinstance(additional_kwargs, Mapping):
        return None
    metadata = additional_kwargs.get("metadata")
    return metadata if isinstance(metadata, Mapping) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
