from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.base import CheckpointTuple

from app.schemas.conversation_refs import JsonValue
from app.services.conversation_protocol_projection import (
    checkpoint_channel_values,
    serialize_checkpoint_value,
)


async def load_checkpoint_values_and_run(
    checkpointer: Any,
    *,
    thread_id: str,
    checkpoint_id: str | None,
) -> tuple[dict[str, Any], str | None]:
    if checkpoint_id is None:
        return {}, None

    config = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    checkpoint_tuple = await _get_checkpoint_tuple(checkpointer, config)
    if checkpoint_tuple is None:
        checkpoint_tuple = await _find_checkpoint_tuple(checkpointer, thread_id, checkpoint_id)
    if checkpoint_tuple is None:
        return {}, None
    metadata = getattr(checkpoint_tuple, "metadata", None)
    run_id = metadata.get("moldy_run_id") if isinstance(metadata, Mapping) else None
    return checkpoint_channel_values(checkpoint_tuple), run_id if isinstance(run_id, str) else None


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


def _channel_values_from_tuple(checkpoint_tuple: CheckpointTuple) -> dict[str, JsonValue]:
    return checkpoint_channel_values(checkpoint_tuple)


_serialize_checkpoint_value = serialize_checkpoint_value
