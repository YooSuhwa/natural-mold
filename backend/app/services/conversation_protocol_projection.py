from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.types import Send
from pydantic import TypeAdapter

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.models.conversation import Conversation
from app.schemas.conversation_history import JsonValue
from app.services.conversation_runtime_policy import snapshot_from_conversation

_JSON = TypeAdapter(JsonValue)
type CheckpointValue = (
    JsonValue
    | BaseMessage
    | Send
    | list[Send]
    | list["CheckpointValue"]
    | Mapping[str, "CheckpointValue"]
)


@dataclass(frozen=True, slots=True)
class MessageProjectionRequest:
    message: BaseMessage
    checkpoint_id: str | None = None
    metadata: Mapping[str, JsonValue] | None = None
    secret_values: Iterable[str] | None = None


def project_todo_policy_values(
    conversation: Conversation,
    values: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    runtime_policy = snapshot_from_conversation(conversation)
    if (
        runtime_policy is None
        or runtime_policy.source != "stored"
        or runtime_policy.effective.todo.enabled
    ):
        return values
    return {key: value for key, value in values.items() if key != "todos"}


def checkpoint_channel_values(checkpoint_tuple: CheckpointTuple) -> dict[str, JsonValue]:
    checkpoint = checkpoint_tuple.checkpoint
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, Mapping):
        return {}
    return {str(key): serialize_checkpoint_value(value) for key, value in channel_values.items()}


def serialize_checkpoint_value(value: CheckpointValue) -> JsonValue:
    if isinstance(value, BaseMessage):
        return _JSON.validate_python(value.model_dump(mode="json"), strict=True)
    if isinstance(value, Send):
        return _JSON.validate_python(
            {
                "node": value.node,
                "arg": value.arg,
                "timeout": value.timeout,
            }
        )
    if isinstance(value, list):
        return [serialize_checkpoint_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): serialize_checkpoint_value(item) for key, item in value.items()}
    return value


def serialize_langchain_message(
    request: MessageProjectionRequest,
) -> dict[str, JsonValue]:
    payload = _JSON.validate_python(request.message.model_dump(mode="json"), strict=True)
    projected = _JSON.validate_python(
        project_and_redact_protocol_data(
            "messages",
            payload,
            secret_values=request.secret_values,
        ),
        strict=True,
    )
    if not isinstance(projected, dict):
        projected = {"type": "unknown", "content": projected}
    if request.checkpoint_id is None and not request.metadata:
        return projected
    return _with_checkpoint_metadata(projected, request.checkpoint_id, request.metadata)


def _with_checkpoint_metadata(
    payload: dict[str, JsonValue],
    checkpoint_id: str | None,
    metadata: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    additional_kwargs = payload.get("additional_kwargs")
    additional_kwargs_dict = (
        dict(additional_kwargs) if isinstance(additional_kwargs, Mapping) else {}
    )
    existing_metadata = additional_kwargs_dict.get("metadata")
    metadata_dict = dict(existing_metadata) if isinstance(existing_metadata, Mapping) else {}
    extra_metadata = dict(metadata or {})
    if checkpoint_id is not None:
        extra_metadata["checkpoint_id"] = checkpoint_id
    return {
        **payload,
        "additional_kwargs": {
            **additional_kwargs_dict,
            "metadata": {**metadata_dict, **extra_metadata},
        },
    }


__all__ = [
    "MessageProjectionRequest",
    "checkpoint_channel_values",
    "project_todo_policy_values",
    "serialize_checkpoint_value",
    "serialize_langchain_message",
]
