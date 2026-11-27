"""Proof that a root graph can continue the same ordinary chat turn after owner loss."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    convert_to_messages,
)
from langgraph.checkpoint.base import CheckpointTuple

from app.agent_runtime.recovery_stream import recovery_stream_cursor
from app.agent_runtime.runtime_config import AgentConfig
from app.chat_recovery.fence import current_run_fence
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

RECOVER_GRAPH_HEAD = object()
_INPUT_RUN_KEY = "moldy_recovery_input_run"


class RecoveryCheckpointBlocked(RuntimeError):
    def __init__(self, code: str = "recovery_checkpoint_unavailable") -> None:
        self.code = code
        super().__init__(code)


def _input_id(run_id: uuid.UUID) -> str:
    return str(uuid.uuid5(run_id, "chat-recovery-input"))


def bind_recoverable_input(messages: list[BaseMessage], run_id: str | None) -> None:
    """Pin only the new user message of an enrolled direct chat."""
    fence = current_run_fence()
    if fence is None:
        return
    if (
        run_id != str(fence.run_id)
        or len(messages) != 1
        or not isinstance(messages[0], HumanMessage)
    ):
        raise RecoveryCheckpointBlocked()
    human = messages[0]
    if human.id is None:
        human.id = _input_id(fence.run_id)
    # The UI may already address this message by its client-generated id. Keep
    # that identity. This server-written marker lives in response metadata,
    # separate from the user's model-facing content and tool arguments.
    human.response_metadata = {**human.response_metadata, _INPUT_RUN_KEY: str(fence.run_id)}


async def bind_recoverable_state_input(
    payload: Any, run_id: str | None, agent: Any, config: dict[str, Any]
) -> Any:
    """Normal chat sends a state dictionary. Bind its human before graph admission."""
    if current_run_fence() is None:
        return payload
    if (
        not isinstance(payload, dict)
        or set(payload) != {"messages"}
        or not isinstance(payload["messages"], list)
    ):
        raise RecoveryCheckpointBlocked()
    messages = convert_to_messages(payload["messages"])
    supplied_id = messages[0].id if len(messages) == 1 else None
    bind_recoverable_input(messages, run_id)
    if supplied_id:
        # add_messages replaces an existing id in place. An edit cannot prove
        # that the tools following the replaced human belong to this run.
        before = await agent.aget_state(config)
        prior = (before.values or {}).get("messages", [])
        if any(message.id == supplied_id for message in prior):
            messages[0].response_metadata[_INPUT_RUN_KEY] = "existing-message"
    return {"messages": messages}


@dataclass(frozen=True, slots=True)
class TurnTools:
    requested: frozenset[str]
    completed: frozenset[str]


def turn_tools(messages: Iterable[BaseMessage], run_id: uuid.UUID) -> TurnTools:
    """Only tool pairs in this server-marked user turn count as completed."""
    anchor = _input_id(run_id)
    in_turn = False
    names: dict[str, str] = {}
    completed: set[str] = set()
    for message in messages:
        if isinstance(message, HumanMessage):
            if in_turn:
                raise RecoveryCheckpointBlocked()
            marker = message.response_metadata.get(_INPUT_RUN_KEY)
            in_turn = marker == str(run_id) or (marker is None and message.id == anchor)
            continue
        if not in_turn:
            continue
        if isinstance(message, AIMessage):
            if message.invalid_tool_calls:
                raise RecoveryCheckpointBlocked()
            for call in message.tool_calls:
                call_id = call.get("id")
                if not call_id or call_id in names:
                    raise RecoveryCheckpointBlocked()
                names[call_id] = call["name"]
        elif isinstance(message, ToolMessage):
            if message.tool_call_id not in names or message.tool_call_id in completed:
                raise RecoveryCheckpointBlocked()
            if message.name == names[message.tool_call_id]:
                completed.add(message.tool_call_id)
    if not in_turn:
        raise RecoveryCheckpointBlocked()
    return TurnTools(frozenset(names), frozenset(completed))


def checked_head(saved: CheckpointTuple | None, cfg: AgentConfig, run_id: uuid.UUID) -> str:
    if saved is None:
        raise RecoveryCheckpointBlocked()
    configurable = saved.config.get("configurable") or {}
    checkpoint_id = configurable.get("checkpoint_id")
    if not (
        isinstance(checkpoint_id, str)
        and checkpoint_id
        and checkpoint_id == saved.checkpoint.get("id")
        and configurable.get("thread_id") == cfg.thread_id
        and configurable.get("checkpoint_ns") == ""
        and saved.metadata.get(CHECKPOINT_TENANT_KEY) == str(cfg.org_context.tenant_id)
        and saved.metadata.get("moldy_run_id") == str(run_id)
        and saved.metadata.get("source") == "loop"
        and not saved.metadata.get("parents")
    ):
        raise RecoveryCheckpointBlocked()
    for _, channel, _ in saved.pending_writes or ():
        if channel in ("__interrupt__", "__resume__", "__error__"):
            raise RecoveryCheckpointBlocked()
    return checkpoint_id


async def verified_resume_config(
    agent: Any, config: dict[str, Any], cfg: AgentConfig, run_id: str | None
) -> dict[str, Any]:
    """Inspect the current head, then resume without LangGraph time travel.

    Passing an historical checkpoint id explicitly can re-execute successful tasks.
    A current-generation saver guards the head and its writes against a prior worker.
    """
    fence = current_run_fence()
    if fence is None or str(fence.run_id) != run_id or str(fence.conversation_id) != cfg.thread_id:
        raise RecoveryCheckpointBlocked()
    configurable = dict(config["configurable"])
    expected = configurable.pop("checkpoint_id", None)
    if not expected or expected != cfg.checkpoint_id:
        raise RecoveryCheckpointBlocked()
    current = {**config, "configurable": configurable}
    state = await agent.aget_state(current)
    head = state.config.get("configurable") or {}
    metadata = state.metadata or {}
    if not (
        head.get("checkpoint_id") == expected
        and head.get("thread_id") == cfg.thread_id
        and head.get("checkpoint_ns") == ""
        and metadata.get("source") == "loop"
        and metadata.get("moldy_run_id") == run_id
        and metadata.get(CHECKPOINT_TENANT_KEY) == str(fence.tenant.tenant_id)
        and not state.interrupts
        and state.tasks
        and all(not (task.error or task.interrupts or task.state) for task in state.tasks)
    ):
        raise RecoveryCheckpointBlocked()
    messages = state.values.get("messages") if isinstance(state.values, dict) else None
    if not isinstance(messages, Sequence) or not all(isinstance(m, BaseMessage) for m in messages):
        raise RecoveryCheckpointBlocked()
    tools = turn_tools(messages, fence.run_id)
    cursor = recovery_stream_cursor(str(fence.run_id), cfg.thread_id)
    if cursor is None or not cursor.visible_tool_call_ids.issubset(tools.requested):
        raise RecoveryCheckpointBlocked("recovery_output_started")
    return current
