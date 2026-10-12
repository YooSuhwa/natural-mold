from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.base import CheckpointTuple

from app.services.conversation_protocol_projection import (
    checkpoint_channel_values,
    serialize_checkpoint_value,
)


async def load_checkpoint_channel_values(
    checkpointer: Any,
    *,
    thread_id: str,
    checkpoint_id: str | None,
) -> dict[str, Any]:
    if checkpoint_id is None:
        return {}

    config = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    checkpoint_tuple = await _get_checkpoint_tuple(checkpointer, config)
    if checkpoint_tuple is None:
        checkpoint_tuple = await _find_checkpoint_tuple(checkpointer, thread_id, checkpoint_id)
    if checkpoint_tuple is None:
        return {}
    return checkpoint_channel_values(checkpoint_tuple)


async def _get_checkpoint_tuple(checkpointer: Any, config: dict[str, Any]) -> Any | None:
    aget_tuple = getattr(checkpointer, "aget_tuple", None)
    if not callable(aget_tuple):
        return None
    try:
        result = aget_tuple(config)
    except NotImplementedError:
        return None
    if not inspect.isawaitable(result):
        return None
    return await result


async def _find_checkpoint_tuple(
    checkpointer: Any,
    thread_id: str,
    checkpoint_id: str,
) -> Any | None:
    async for checkpoint_tuple in checkpointer.alist({"configurable": {"thread_id": thread_id}}):
        config = getattr(checkpoint_tuple, "config", None)
        if not isinstance(config, Mapping):
            continue
        configurable = config.get("configurable")
        if not isinstance(configurable, Mapping):
            continue
        if configurable.get("checkpoint_id") == checkpoint_id:
            return checkpoint_tuple
    return None


def _channel_values_from_tuple(checkpoint_tuple: CheckpointTuple) -> dict[str, Any]:
    return checkpoint_channel_values(checkpoint_tuple)


_serialize_checkpoint_value = serialize_checkpoint_value
