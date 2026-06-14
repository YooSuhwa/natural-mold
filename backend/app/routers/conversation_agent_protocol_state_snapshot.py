from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_checkpoint_state import (
    load_checkpoint_channel_values,
)
from app.routers.conversation_agent_protocol_legacy import legacy_state_messages
from app.services.thread_branch_service import _collect_checkpoints, build_message_tree


@dataclass(frozen=True)
class ThreadStateSnapshot:
    values: dict[str, Any]
    checkpoint_by_message_id: dict[str, str]


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


async def load_thread_state_snapshot(
    conversation: Conversation,
    db: AsyncSession | None = None,
) -> ThreadStateSnapshot:
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
        return await _legacy_thread_state_snapshot(conversation, db)
    if not tree.nodes:
        return await _legacy_thread_state_snapshot(conversation, db)

    values = await load_checkpoint_channel_values(
        checkpointer,
        thread_id=str(conversation.id),
        checkpoint_id=tree.active_checkpoint_id,
    )
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
        values={**values, "messages": messages},
        checkpoint_by_message_id=checkpoint_by_message_id,
    )


async def _legacy_thread_state_snapshot(
    conversation: Conversation,
    db: AsyncSession | None,
) -> ThreadStateSnapshot:
    if db is None:
        return ThreadStateSnapshot(values={}, checkpoint_by_message_id={})
    messages = await legacy_state_messages(db, conversation.id)
    return ThreadStateSnapshot(values={"messages": messages}, checkpoint_by_message_id={})


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
