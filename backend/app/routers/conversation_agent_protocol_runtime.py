from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    format_protocol_sse,
    matches_subscription,
    stored_protocol_event,
)
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_contracts import AgentCommandRequest
from app.services import chat_service
from app.services.thread_branch_service import _collect_checkpoints, build_message_tree

SUPPORTED_MULTITASK_STRATEGIES = {"reject"}


@dataclass(frozen=True)
class ThreadStateSnapshot:
    values: dict[str, Any]
    checkpoint_by_message_id: dict[str, str]


async def get_owned_thread(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    thread_id: str,
    user_id: uuid.UUID,
) -> Conversation:
    if thread_id != str(conversation_id):
        raise conversation_not_found()

    conversation = await chat_service.get_owned_conversation(db, conversation_id, user_id)
    if conversation is None:
        raise conversation_not_found()
    return conversation


def cfg_agent_uuid(conversation: Conversation) -> uuid.UUID:
    return conversation.agent_id


def serialize_langchain_message(
    message: Any,
    *,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        dumped = message.model_dump(mode="json")
        payload = dumped if isinstance(dumped, dict) else {"type": "unknown", "content": dumped}
    else:
        get_type = getattr(message, "_getType", lambda: "unknown")
        payload = {
            "type": str(getattr(message, "type", None) or get_type()),
            "content": getattr(message, "content", ""),
            "id": getattr(message, "id", None),
        }
    if checkpoint_id is None:
        return payload
    return _with_checkpoint_metadata(payload, checkpoint_id)


async def load_thread_state_values(conversation: Conversation) -> dict[str, Any]:
    return (await load_thread_state_snapshot(conversation)).values


async def load_thread_state_snapshot(conversation: Conversation) -> ThreadStateSnapshot:
    try:
        checkpointer = get_checkpointer()
        tree = await build_message_tree(
            checkpointer,
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )
        checkpoint_by_message_id = await _checkpoint_by_message_id(
            checkpointer,
            str(conversation.id),
        )
    except RuntimeError:
        return ThreadStateSnapshot(values={}, checkpoint_by_message_id={})
    if not tree.nodes:
        return ThreadStateSnapshot(values={}, checkpoint_by_message_id={})

    messages: list[dict[str, Any]] = []
    for node in tree.nodes:
        message_id = _message_id_from_message(node.message)
        checkpoint_id = (
            checkpoint_by_message_id.get(message_id)
            if message_id is not None
            else node.introduced_by_checkpoint_id
        )
        checkpoint_id = checkpoint_id or node.introduced_by_checkpoint_id
        payload = serialize_langchain_message(node.message, checkpoint_id=checkpoint_id)
        messages.append(payload)
        if message_id is not None:
            checkpoint_by_message_id[message_id] = checkpoint_id

    return ThreadStateSnapshot(
        values={"messages": messages},
        checkpoint_by_message_id=checkpoint_by_message_id,
    )


def _with_checkpoint_metadata(payload: dict[str, Any], checkpoint_id: str) -> dict[str, Any]:
    additional_kwargs = payload.get("additional_kwargs")
    additional_kwargs_dict = (
        dict(additional_kwargs) if isinstance(additional_kwargs, Mapping) else {}
    )
    metadata = additional_kwargs_dict.get("metadata")
    metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
    return {
        **payload,
        "additional_kwargs": {
            **additional_kwargs_dict,
            "metadata": {
                **metadata_dict,
                "checkpoint_id": checkpoint_id,
            },
        },
    }


async def _checkpoint_by_message_id(checkpointer: Any, thread_id: str) -> dict[str, str]:
    checkpoints = await _collect_checkpoints(checkpointer, thread_id)
    result: dict[str, str] = {}
    for checkpoint in sorted(checkpoints, key=lambda item: len(item.messages)):
        for message in checkpoint.messages:
            message_id = _message_id_from_message(message)
            if message_id is not None and message_id not in result:
                result[message_id] = checkpoint.checkpoint_id
    return result


def _message_id_from_message(message: Any) -> str | None:
    value = getattr(message, "id", None)
    return value if isinstance(value, str) and value else None


def command_multitask_strategy(command: AgentCommandRequest) -> str:
    return command.params.multitask_strategy or "reject"


def string_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts) if parts else None


def input_preview(input_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(input_payload, Mapping):
        return None
    messages = input_payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get("role") or message.get("type")
        if role not in {"user", "human"}:
            continue
        return string_content(message.get("content"))
    return None


def checkpoint_id(command: AgentCommandRequest) -> str | None:
    checkpoint = command.params.checkpoint
    if checkpoint is None:
        return None
    return checkpoint.checkpoint_id


def _int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _namespace(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [segment for segment in value if isinstance(segment, str)]


def protocol_event_from_broker(
    event: BrokeredEvent,
    *,
    run_id: str,
    thread_id: str,
) -> StoredProtocolEvent | None:
    payload = event.get("data")
    if not isinstance(payload, Mapping):
        return None
    method = _optional_str(payload.get("method"))
    seq = _int_value(payload.get("seq"))
    params = payload.get("params")
    if method is None or seq is None or not isinstance(params, Mapping):
        return None
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method=method,
        namespace=_namespace(params.get("namespace")),
        data=params.get("data"),
        event_id=_optional_str(payload.get("event_id")),
    )


async def protocol_broker_generator(
    broker: EventBroker,
    *,
    thread_id: str,
    params: dict[str, Any],
    after_id: str | None,
) -> AsyncGenerator[str, None]:
    async for event in broker.subscribe(after_id=after_id):
        protocol_event = protocol_event_from_broker(
            event,
            run_id=broker.run_id,
            thread_id=thread_id,
        )
        if protocol_event is not None and matches_subscription(protocol_event, params):
            yield format_protocol_sse(protocol_event)
