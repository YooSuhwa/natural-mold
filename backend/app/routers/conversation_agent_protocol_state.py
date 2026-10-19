from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.types import Send
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.executor import _prepare_agent
from app.agent_runtime.mcp_app_projection import (
    extract_mcp_app_candidates,
    project_verified_mcp_apps,
    verified_mcp_app_artifacts,
)
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_contracts import (
    HistoryRequest,
    ThreadCheckpoint,
    UpdateStateRequest,
    state_response,
)
from app.routers.conversation_agent_protocol_state_snapshot import (
    project_todo_policy_values,
)
from app.schemas.conversation_history import ConversationHistoryCheckpointResponse, JsonValue
from app.schemas.conversation_history_protocol import (
    ThreadStateResponse,
    ThreadStateResponseInput,
    build_state_response,
)
from app.schemas.conversation_scope import ConversationBranchMutation
from app.services.chat.conversation_mutations import set_active_branch
from app.services.chat.conversations import require_executable_conversation
from app.services.conversation_protocol_history import (
    HistoryCheckpointProjectionRequest,
    LiveThreadHistoryRequest,
    build_checkpoint_history_response,
    load_live_thread_history,
)
from app.services.conversation_stream_service import resolve_agent_context
from app.services.thread_branch_service import _CheckpointSlim

INITIAL_UPDATE_NODE = "__start__"
DEFAULT_UPDATE_NODE = "agent"


async def load_thread_history_response(
    conversation: Conversation,
    request: HistoryRequest,
    *,
    db: AsyncSession | None = None,
    user: CurrentUser | None = None,
    context: OrgContext,
) -> list[ThreadStateResponse]:
    if conversation.agent_lifecycle_state == "purged":
        if db is None:
            return []
        from app.services.conversation_history_service import read_purged_history_content

        content = await read_purged_history_content(db, conversation)
        retained = [
            ConversationHistoryCheckpointResponse.model_validate(item)
            for item in content.checkpoints
        ]
        checkpoint_by_message_id: dict[str, str] = {}
        for checkpoint in retained:
            for message in checkpoint.messages:
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id:
                    checkpoint_by_message_id.setdefault(message_id, checkpoint.checkpoint_id)
        responses: list[ThreadStateResponse] = []
        for checkpoint in retained:
            values: dict[str, JsonValue] = dict(checkpoint.channel_values)
            values["messages"] = list(checkpoint.messages)
            responses.append(
                build_state_response(
                    conversation,
                    ThreadStateResponseInput(
                        values=values,
                        checkpoint_id=checkpoint.checkpoint_id,
                        checkpoint_ns=checkpoint.checkpoint_namespace,
                        checkpoint_by_message_id=checkpoint_by_message_id,
                        metadata_source="moldy_retained_history",
                    ),
                )
            )
        return responses
    return list(
        await load_live_thread_history(
            LiveThreadHistoryRequest(
                conversation,
                request,
                context,
                db,
                user,
                get_checkpointer,
            )
        )
    )


async def update_thread_state_response(
    db: AsyncSession,
    *,
    conversation: Conversation,
    request: UpdateStateRequest,
    user: CurrentUser,
    context: OrgContext,
) -> ThreadStateResponse:
    await require_executable_conversation(db, conversation.id, context)
    cfg = await resolve_agent_context(
        db,
        conversation.id,
        user,
        context=context,
        checkpoint_id=request.checkpoint.checkpoint_id if request.checkpoint else None,
    )
    agent, _, config = await _prepare_agent(cfg, messages_history=[])
    update_config = _config_with_checkpoint(
        config,
        thread_id=str(conversation.id),
        checkpoint=request.checkpoint,
    )
    snapshot = await agent.aget_state(update_config)
    values = project_todo_policy_values(conversation, request.values or {"messages": []})
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
        await set_active_branch(
            db, ConversationBranchMutation(conversation.id, context, checkpoint_id)
        )
        await db.commit()
    # ADR-021 C2 — cfg was already resolved above; reuse its eager secret set
    # to mask the returned state values (this endpoint runs outside any run).
    secrets = tuple(collect_cfg_secret_values(cfg))
    return await _snapshot_state_response(conversation, updated, secret_values=secrets)


async def _checkpoint_state_response(
    conversation: Conversation,
    *,
    checkpoint: _CheckpointSlim,
    checkpoint_by_message_id: dict[str, str],
    secret_values: Sequence[str] | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> ThreadStateResponse:
    return await build_checkpoint_history_response(
        HistoryCheckpointProjectionRequest(
            conversation,
            checkpoint,
            checkpoint_by_message_id,
            secret_values,
            session_factory,
        )
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


async def _snapshot_state_response(
    conversation: Conversation,
    snapshot: Any,
    *,
    secret_values: Sequence[str] | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> ThreadStateResponse:
    configurable = _snapshot_configurable(snapshot)
    checkpoint_id = configurable.get("checkpoint_id")
    checkpoint_ns = configurable.get("checkpoint_ns")
    values = project_todo_policy_values(
        conversation,
        _snapshot_values(snapshot, secret_values=secret_values),
    )
    messages = values.get("messages")
    if isinstance(messages, list):
        candidates = extract_mcp_app_candidates(messages)
        values = {
            **values,
            "messages": project_verified_mcp_apps(
                messages,
                await verified_mcp_app_artifacts(
                    messages,
                    expected_conversation_id=str(conversation.id),
                    session_factory=session_factory,
                ),
                untrusted_tool_call_ids={item.tool_call_id for item in candidates},
            ),
        }
    return state_response(
        conversation,
        values=values,
        next_nodes=_string_list(getattr(snapshot, "next", None)),
        tasks=_snapshot_tasks(snapshot, secret_values=secret_values),
        checkpoint_id=checkpoint_id if isinstance(checkpoint_id, str) else None,
        checkpoint_ns=checkpoint_ns if isinstance(checkpoint_ns, str) else "",
        metadata_source="langgraph_state",
        created_at=_optional_str(getattr(snapshot, "created_at", None)),
    )


def _snapshot_values(
    snapshot: Any, *, secret_values: Sequence[str] | None = None
) -> dict[str, Any]:
    values = project_and_redact_protocol_data(
        "values",
        _serialize_value(getattr(snapshot, "values", {}) or {}),
        secret_values=secret_values,
    )
    return values if isinstance(values, dict) else {}


def _snapshot_tasks(
    snapshot: Any,
    *,
    secret_values: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    raw_tasks = getattr(snapshot, "tasks", ()) or ()
    tasks: list[dict[str, Any]] = []
    for task in raw_tasks:
        tasks.append(
            {
                "id": _task_value(task, "id"),
                "name": _task_value(task, "name"),
                "error": _task_value(task, "error"),
                "interrupts": _serialize_value(_task_value(task, "interrupts") or []),
                "checkpoint": _serialize_value(_task_value(task, "checkpoint")),
                "state": _serialize_value(_task_value(task, "state")),
            }
        )
    projected = project_and_redact_protocol_data(
        "tasks",
        tasks,
        secret_values=secret_values,
    )
    return projected if isinstance(projected, list) else []


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
    if isinstance(value, Send):
        return {
            "node": value.node,
            "arg": _serialize_value(value.arg),
            "timeout": value.timeout,
        }
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
