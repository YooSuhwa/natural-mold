"""Read pre-v3 chat records using the same public projector as current runs."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from uuid import UUID, uuid5

from app.agent_runtime import event_names
from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES, MEMORY_TOOL_NAMES
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.agent_runtime.protocol_redaction import REDACTED_MEMORY_FIELD
from app.models.message_event import MessageEvent
from app.schemas.conversation_history import JsonValue


def _uuid_string(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def legacy_journal_events(
    record: MessageEvent,
    raw: Mapping[str, JsonValue],
    ordinal: int,
    secret_values: Collection[str],
) -> tuple[StoredProtocolEvent, ...] | None:
    """Translate a stored SSE row. None requests a canonical history snapshot.

    An empty tuple advances the durable source cursor without publishing data.
    The run row and its checkpoint determine termination and pending approvals.
    """
    name = raw.get("event")
    if not isinstance(name, str):
        return None
    if name in {event_names.MESSAGE_START, event_names.INTERRUPT, event_names.STALE}:
        # message_start.input is a debug copy, which can include the entire prompt.
        return ()
    data = raw.get("data")
    if not isinstance(data, dict):
        return None

    payload: dict[str, JsonValue]
    if name in {event_names.CONTENT_DELTA, event_names.MESSAGE_END}:
        is_delta = name == event_names.CONTENT_DELTA
        content = data.get("delta" if is_delta else "content")
        if not is_delta and content is None:
            return ()
        if not isinstance(content, str):
            return None
        if not content:
            return ()
        # Old tokens have no per-model ID. Derive one for the visible turn that
        # remains stable when finalize_turn later populates linked_message_ids.
        message_id = uuid5(
            record.conversation_id, f"ag-ui:legacy-assistant:{record.assistant_msg_id}"
        )
        method = "messages"
        # message_end holds the full answer, sometimes including a final suffix
        # that never appeared in a delta. The projector reconciles full messages.
        payload = {
            "type": "AIMessageChunk" if is_delta else "ai",
            "id": str(message_id),
            "content": content,
        }
    elif name in {event_names.TOOL_CALL_START, event_names.TOOL_CALL_RESULT}:
        tool_name, call_id = data.get("tool_name"), data.get("tool_call_id")
        if (
            not isinstance(tool_name, str)
            or not tool_name
            or not isinstance(call_id, str)
            or not call_id
        ):
            # A guessed association can assign a result to the wrong parallel call.
            return None
        if tool_name == "ToolSelectionResponse":
            return ()
        method = "tools"
        payload = {"name": tool_name, "tool_call_id": call_id}
        if name == event_names.TOOL_CALL_START:
            args = data.get("parameters", {})
            if not isinstance(args, dict):
                return None
            payload.update(event="tool-started", args=args)
        else:
            result = data.get("result")
            if not isinstance(result, str):
                return None
            if tool_name in MEMORY_TOOL_NAMES:
                # Older writers can contain the unredacted memory in JSON text.
                # Its separately scoped event supplies the reference for a fresh lookup.
                result = REDACTED_MEMORY_FIELD
            payload.update(event="tool-finished", content=result)
    elif name == event_names.FILE_EVENT:
        artifact_id, version_id = _uuid_string(data.get("id")), _uuid_string(data.get("version_id"))
        if (
            not artifact_id
            or not version_id
            or data.get("conversation_id") != str(record.conversation_id)
            or data.get("run_id") not in (None, record.assistant_msg_id)
        ):
            return None
        op = data.get("op")
        if not isinstance(op, str) or op not in {"created", "updated", "deleted", "failed"}:
            return None
        method = "custom"
        payload = {"name": name, "payload": {"id": artifact_id, "version_id": version_id, "op": op}}
    elif name in MEMORY_EVENT_NAMES:
        if data.get("conversation_id") not in (None, str(record.conversation_id)):
            return None
        memory_id = _uuid_string(data.get("id"))
        if not memory_id:
            return ()
        method = "custom"
        payload = {"name": name, "payload": {"id": memory_id}}
    else:
        # Error details belong to the authoritative outcome. Unrecognized
        # application/debug events have no public representation.
        return ()

    raw_id = raw.get("id")
    event_id = raw_id if isinstance(raw_id, str) and raw_id else None
    public_data = project_and_redact_protocol_data(method, payload, secret_values=secret_values)
    return (
        stored_protocol_event(
            run_id=record.assistant_msg_id,
            thread_id=str(record.conversation_id),
            seq=ordinal,
            method=method,
            data=public_data,
            event_id=event_id,
        ),
    )
