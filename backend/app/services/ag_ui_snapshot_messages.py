"""Project saved root messages into the public AG-UI history representation."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.messages import BaseMessage

from app.agent_runtime.message_utils import content_to_text, parse_msg_id
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.streaming import _is_tool_selector_json
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER

if TYPE_CHECKING:
    from app.services.ag_ui_snapshot_history import SnapshotMessageRef


_ROLES = {
    "ai": "assistant",
    "assistant": "assistant",
    "human": "user",
    "user": "user",
    "tool": "tool",
}
_MODEL_INPUT_NAMES = frozenset({"moldy_recalled_memories", "moldy_conversation_goal"})


def _saved_field(message: BaseMessage | Mapping[str, Any], key: str) -> Any:
    return message.get(key) if isinstance(message, Mapping) else getattr(message, key, None)


def _visible_message(
    message: BaseMessage | Mapping[str, Any],
    *,
    fallback_id: str,
    secret_values: Collection[str],
) -> dict[str, Any] | None:
    raw_role = _saved_field(message, "type") or _saved_field(message, "role")
    role = _ROLES.get(raw_role) if isinstance(raw_role, str) else None
    if role is None:
        return None
    name = _saved_field(message, "name")
    extra = _saved_field(message, "additional_kwargs")
    response = _saved_field(message, "response_metadata")
    # Harness notifications (including summaries) can have a human *role*.
    # The two named model-only notes are normally injected after checkpointing.
    if (
        (isinstance(name, str) and name in _MODEL_INPUT_NAMES)
        or (isinstance(extra, Mapping) and extra.get("lc_source"))
        or (isinstance(response, Mapping) and response.get(STEER_INPUT_MARKER))
    ):
        return None
    raw_id = _saved_field(message, "id")
    result: dict[str, Any] = {
        "id": raw_id if isinstance(raw_id, str) and raw_id else fallback_id,
        "role": role,
    }
    if role == "tool":
        call_id = _saved_field(message, "tool_call_id")
        if not isinstance(call_id, str) or not call_id or name == "ToolSelectionResponse":
            return None
        # The journal's public tool result is authoritative. A checkpoint may hold
        # private documents, memory, model-only feedback or an unverified MCP widget.
        result.update(toolCallId=call_id, content="")
    else:
        content = _saved_field(message, "content")
        result["content"] = (
            content_to_text(content) if isinstance(content, (str, dict, list)) else ""
        )
        # The selector occasionally returns its schema as an assistant text response.
        if (
            role == "assistant"
            and '"tools"' in result["content"]
            and _is_tool_selector_json(result["content"])
        ):
            result["content"] = ""
        calls = _saved_field(message, "tool_calls")
        if role == "assistant" and isinstance(calls, list):
            tool_calls = []
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                call_id, tool_name = call.get("id"), call.get("name")
                if (
                    isinstance(call_id, str)
                    and call_id
                    and isinstance(tool_name, str)
                    and tool_name
                ):
                    if tool_name == "ToolSelectionResponse":
                        continue
                    tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": "{}"},
                        }
                    )
            if tool_calls:
                result["toolCalls"] = tool_calls
        if role == "assistant" and not result["content"] and "toolCalls" not in result:
            return None
    return project_and_redact_protocol_data("messages", result, secret_values=secret_values)


def public_checkpoint_message(
    ref: SnapshotMessageRef, conversation_id: UUID, *, secret_values: Collection[str]
) -> dict[str, Any] | None:
    """Keep the original root message position for history without durable IDs."""
    return _visible_message(
        ref.message,
        fallback_id=str(parse_msg_id(None, conversation_id, ref.original_index)),
        secret_values=secret_values,
    )


def legacy_user_messages(
    raw: Mapping[str, Any],
    *,
    conversation_id: UUID,
    physical_run_id: UUID | str,
    secret_values: Collection[str],
) -> list[dict[str, Any]]:
    """Recover the submitting user's turn from a scoped legacy entry's start frame.

    Start payloads can also contain prior turns, system context or a resume command.
    The terminal human input is the only entry attributable to this submission.
    """
    if raw.get("event") != "message_start":
        return []
    data = raw.get("data")
    if not isinstance(data, Mapping) or data.get("id") not in (None, str(physical_run_id)):
        return []
    for envelope in (raw, data):
        if (
            envelope.get("run_id") not in (None, str(physical_run_id))
            or envelope.get("thread_id") not in (None, str(conversation_id))
            or envelope.get("namespace") not in (None, [], "")
            or envelope.get("checkpoint_ns") not in (None, "")
        ):
            return []
    payload = data.get("input")
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], Mapping):
        return []
    submitted = messages[-1]
    if (submitted.get("type") or submitted.get("role")) not in ("human", "user"):
        return []
    # Match the stable legacy-state ID when the old trace had no client message ID.
    index = len(messages) - 1
    result = _visible_message(
        submitted,
        fallback_id=f"legacy:{physical_run_id}:input:{index}",
        secret_values=secret_values,
    )
    return [result] if result else []
