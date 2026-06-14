from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.executor import _prepare_agent
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_contracts import (
    HistoryRequest,
    ThreadCheckpoint,
    ThreadHistoryCursor,
    UpdateStateRequest,
    state_response,
)
from app.routers.conversation_agent_protocol_state_snapshot import (
    serialize_langchain_message,
)
from app.services.conversation_stream_service import resolve_agent_context
from app.services.thread_branch_service import _CheckpointSlim, _collect_checkpoints

INITIAL_UPDATE_NODE = "__start__"
DEFAULT_UPDATE_NODE = "agent"


async def load_thread_history_response(
    conversation: Conversation,
    request: HistoryRequest,
) -> list[dict[str, object]]:
    try:
        checkpointer = get_checkpointer()
    except RuntimeError:
        return []

    checkpoints = await _collect_checkpoints(checkpointer, str(conversation.id))
    checkpoint_by_message_id = _checkpoint_by_message_id_from_checkpoints(checkpoints)
    return [
        _checkpoint_state_response(
            conversation,
            checkpoint=checkpoint,
            checkpoint_by_message_id=checkpoint_by_message_id,
        )
        for checkpoint in _page_checkpoints(
            checkpoints,
            before=request.before,
            limit=request.limit,
        )
    ]


async def update_thread_state_response(
    db: AsyncSession,
    *,
    conversation: Conversation,
    request: UpdateStateRequest,
    user: CurrentUser,
) -> dict[str, object]:
    cfg = await resolve_agent_context(
        db,
        conversation.id,
        user,
        checkpoint_id=request.checkpoint.checkpoint_id if request.checkpoint else None,
    )
    agent, _, config = await _prepare_agent(cfg, messages_history=[])
    update_config = _config_with_checkpoint(
        config,
        thread_id=str(conversation.id),
        checkpoint=request.checkpoint,
    )
    snapshot = await agent.aget_state(update_config)
    values = request.values or {"messages": []}
    await agent.aupdate_state(
        update_config,
        values,
        as_node=_resolve_update_node(
            as_node=request.as_node,
            values=values,
            has_checkpoint=_thread_has_checkpoint(snapshot),
        ),
        task_id=request.task_id,
    )
    updated = await agent.aget_state({"configurable": {"thread_id": str(conversation.id)}})
    checkpoint_id = _snapshot_checkpoint_id(updated)
    if checkpoint_id:
        conversation.active_branch_checkpoint_id = checkpoint_id
        await db.commit()
    return _snapshot_state_response(conversation, updated)


def _page_checkpoints(
    checkpoints: Sequence[_CheckpointSlim],
    *,
    before: ThreadHistoryCursor | None,
    limit: int,
) -> list[_CheckpointSlim]:
    before_id = _history_cursor_checkpoint_id(before)
    page: list[_CheckpointSlim] = []
    past_before = before_id is None
    for checkpoint in checkpoints:
        if not past_before:
            if checkpoint.checkpoint_id == before_id:
                past_before = True
            continue
        page.append(checkpoint)
        if len(page) >= limit:
            break
    return page


def _history_cursor_checkpoint_id(before: ThreadHistoryCursor | None) -> str | None:
    if before is None:
        return None
    if before.checkpoint_id:
        return before.checkpoint_id
    configurable = before.configurable
    return configurable.checkpoint_id if configurable is not None else None


def _checkpoint_by_message_id_from_checkpoints(
    checkpoints: Sequence[_CheckpointSlim],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for checkpoint in sorted(checkpoints, key=lambda item: len(item.messages)):
        for message in checkpoint.messages:
            message_id = _message_id_from_message(message)
            if message_id is not None and message_id not in result:
                result[message_id] = checkpoint.checkpoint_id
    return result


def _checkpoint_state_response(
    conversation: Conversation,
    *,
    checkpoint: _CheckpointSlim,
    checkpoint_by_message_id: dict[str, str],
) -> dict[str, object]:
    messages: list[dict[str, Any]] = []
    for message in checkpoint.messages:
        message_id = _message_id_from_message(message)
        introduced_by = (
            checkpoint_by_message_id.get(message_id)
            if message_id is not None
            else checkpoint.checkpoint_id
        )
        messages.append(
            serialize_langchain_message(
                message,
                checkpoint_id=introduced_by or checkpoint.checkpoint_id,
            )
        )
    return state_response(
        conversation,
        values={"messages": messages},
        checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_by_message_id=checkpoint_by_message_id,
        metadata_source="moldy_checkpointer_history",
    )


def _config_with_checkpoint(
    config: dict[str, Any],
    *,
    thread_id: str,
    checkpoint: ThreadCheckpoint | None,
) -> dict[str, Any]:
    configurable = dict(config.get("configurable") or {})
    configurable["thread_id"] = thread_id
    if checkpoint is not None and checkpoint.checkpoint_id:
        configurable["checkpoint_id"] = checkpoint.checkpoint_id
        if checkpoint.checkpoint_ns is not None:
            configurable["checkpoint_ns"] = checkpoint.checkpoint_ns
    return {**config, "configurable": configurable}


def _resolve_update_node(
    *,
    as_node: str | None,
    values: dict[str, Any],
    has_checkpoint: bool,
) -> str:
    if as_node:
        return as_node
    messages = values.get("messages")
    if not messages:
        return INITIAL_UPDATE_NODE
    if not has_checkpoint:
        return INITIAL_UPDATE_NODE
    return DEFAULT_UPDATE_NODE


def _snapshot_state_response(
    conversation: Conversation,
    snapshot: Any,
) -> dict[str, object]:
    configurable = _snapshot_configurable(snapshot)
    checkpoint_id = configurable.get("checkpoint_id")
    checkpoint_ns = configurable.get("checkpoint_ns")
    values = _snapshot_values(snapshot)
    return state_response(
        conversation,
        values=values,
        next_nodes=_string_list(getattr(snapshot, "next", None)),
        tasks=_snapshot_tasks(snapshot),
        checkpoint_id=checkpoint_id if isinstance(checkpoint_id, str) else None,
        checkpoint_ns=checkpoint_ns if isinstance(checkpoint_ns, str) else "",
        metadata_source="langgraph_state",
        created_at=_optional_str(getattr(snapshot, "created_at", None)),
    )


def _snapshot_values(snapshot: Any) -> dict[str, Any]:
    values = redact_protocol_data("values", _serialize_value(getattr(snapshot, "values", {}) or {}))
    return values if isinstance(values, dict) else {}


def _snapshot_tasks(snapshot: Any) -> list[dict[str, Any]]:
    raw_tasks = getattr(snapshot, "tasks", ()) or ()
    tasks: list[dict[str, Any]] = []
    for task in raw_tasks:
        tasks.append(
            {
                "id": _task_value(task, "id"),
                "name": _task_value(task, "name"),
                "error": _task_value(task, "error"),
                "interrupts": _task_value(task, "interrupts") or [],
                "checkpoint": _task_value(task, "checkpoint"),
                "state": _task_value(task, "state"),
            }
        )
    return tasks


def _task_value(task: Any, key: str) -> Any:
    if isinstance(task, Mapping):
        return task.get(key)
    return getattr(task, key, None)


def _thread_has_checkpoint(snapshot: Any) -> bool:
    checkpoint_id = _snapshot_checkpoint_id(snapshot)
    return checkpoint_id is not None and bool(checkpoint_id)


def _snapshot_checkpoint_id(snapshot: Any) -> str | None:
    value = _snapshot_configurable(snapshot).get("checkpoint_id")
    return value if isinstance(value, str) else None


def _snapshot_configurable(snapshot: Any) -> Mapping[str, Any]:
    config = getattr(snapshot, "config", None)
    if not isinstance(config, Mapping):
        return {}
    configurable = config.get("configurable")
    return configurable if isinstance(configurable, Mapping) else {}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, BaseMessage):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


def _message_id_from_message(message: Any) -> str | None:
    value = getattr(message, "id", None)
    return value if isinstance(value, str) and value else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
