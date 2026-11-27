"""Identify a paused Deep Agents task from its issuing graph and its child checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage
from langgraph._internal._constants import PUSH, TASKS
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.pregel._algo import _uuid5_str, _xxhash_str
from langgraph.pregel._task_status import read_task_statuses
from langgraph.types import Interrupt, Send

from app.agent_runtime.delegated_child_checkpoint import _checkpoint_messages
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY


@dataclass(frozen=True, slots=True)
class LegacyChildBinding:
    runtime_name: str
    tool_call_id: str
    checkpoint_ns: str
    producer_run_id: UUID | None


def _tool_dispatches(saved: CheckpointTuple) -> dict[str, list[Any]] | None:
    """Reproduce prepare_push_task_send's native identity for each stored Send to ToolNode."""
    channels = saved.checkpoint["channel_values"]
    sends = channels.get(TASKS)
    if not isinstance(sends, (tuple, list)):
        return {}
    step = saved.metadata.get("step")
    version = saved.checkpoint.get("v")
    if type(step) is not int or type(version) is not int:
        return None
    try:
        seed = UUID(saved.checkpoint["id"]).bytes
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    task_id = _xxhash_str if version > 1 else _uuid5_str
    ns = saved.config.get("configurable", {}).get("checkpoint_ns", "")
    node = f"{ns}|tools" if ns else "tools"
    return {
        task_id(seed, node, str(step + 1), "tools", PUSH, str(index)): packet.arg
        for index, packet in enumerate(sends)
        if isinstance(packet, Send) and packet.node == "tools" and isinstance(packet.arg, list)
    }


def _contains_task(calls: list[Any]) -> bool:
    return any(isinstance(call, Mapping) and call.get("name") == "task" for call in calls)


def _pending(saved: CheckpointTuple) -> dict[str, tuple[str, Interrupt]]:
    found: dict[str, tuple[str, Interrupt]] = {}
    try:
        statuses = read_task_statuses(saved.pending_writes or ())
    except (TypeError, ValueError, AttributeError, IndexError):
        return {}
    for task_id, status in statuses.items():
        for value in status.pending_interrupts:
            if not isinstance(value, Interrupt) or value.id in found:
                return {}
            found[value.id] = (task_id, value)
    return found


def _call_matches(message: AIMessage | None, call_id: str, role: str) -> bool:
    if message is None:
        return False
    matching = [call for call in message.tool_calls if call.get("id") == call_id]
    return (
        len(matching) == 1
        and matching[0].get("name") == "task"
        and matching[0]["args"].get("subagent_type") == role
    )


def _proven_child(
    root: CheckpointTuple,
    child: CheckpointTuple,
    *,
    call: Mapping[str, Any],
    namespace: str,
    thread: str,
    tenant: str,
    lineage: Mapping[str, UUID],
    message: AIMessage | None,
) -> LegacyChildBinding | None:
    args = call.get("args")
    role = args.get("subagent_type") if isinstance(args, Mapping) else None
    call_id = call.get("id")
    if not isinstance(role, str) or not role or not isinstance(call_id, str) or not call_id:
        return None
    stored = child.config.get("configurable", {})
    meta = child.metadata
    parents = meta.get("parents")
    if (
        stored.get("thread_id") != thread
        or stored.get("checkpoint_ns") != namespace
        or not stored.get("checkpoint_id")
        or stored["checkpoint_id"] != child.checkpoint["id"]
        or not isinstance(parents, Mapping)
        or parents.get("") != root.checkpoint["id"]
        or meta.get("ls_agent_type") != "subagent"
        or meta.get("lc_agent_name") != role
        or meta.get("lc_subagent_tool_call_id", call_id) != call_id
        or not _call_matches(message, call_id, role)
    ):
        return None
    producer = None
    for source in (root.metadata, meta):
        if source.get(CHECKPOINT_TENANT_KEY, tenant) != tenant:
            return None
        if "moldy_run_id" in source:
            stamp = source["moldy_run_id"]
            if not isinstance(stamp, str) or stamp not in lineage:
                return None
            # The root issued this task. Its child may have re-asked in a later resume.
            producer = producer or lineage[stamp]
    return LegacyChildBinding(role, call_id, namespace, producer)


async def read_legacy_child_bindings(
    saver: BaseCheckpointSaver[Any],
    saved: CheckpointTuple,
    *,
    root_task_ids: Mapping[str, str],
    tenant_id: UUID,
    lineage: set[UUID],
) -> dict[str, LegacyChildBinding | None]:
    """Return a binding only for a dispatched subagent with the same live interrupt.

    A present key with value None means a delegated slot lacks usable proof. An
    ordinary root tool requires no subordinate authority and has no entry.
    """
    thread = saved.config.get("configurable", {}).get("thread_id")
    if not isinstance(thread, str) or not thread:
        return dict.fromkeys(root_task_ids)
    dispatches = _tool_dispatches(saved)
    if dispatches is None:
        return dict.fromkeys(root_task_ids)
    native = _pending(saved)
    groups: dict[str, list[str]] = {}
    for ident, task_id in root_task_ids.items():
        groups.setdefault(task_id, []).append(ident)

    result: dict[str, LegacyChildBinding | None] = {}
    message: AIMessage | None = None
    has_calls = any(_contains_task(calls) for calls in dispatches.values())
    if has_calls:
        try:
            history = await _checkpoint_messages(saver, saved)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            # An unreadable retained reducer cannot establish a delegated identity.
            history = ()
        message = next((item for item in reversed(history) if isinstance(item, AIMessage)), None)
    owners = {str(key): key for key in lineage}
    for task_id, ids in groups.items():
        calls = dispatches.get(task_id)
        if calls is not None and not _contains_task(calls):
            continue
        namespace = f"tools:{task_id}"
        child = await saver.aget_tuple(
            {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}
        )
        if (
            calls is None
            and (child is None or child.metadata.get("ls_agent_type") != "subagent")
            and not has_calls
        ):
            continue
        result.update(dict.fromkeys(ids))
        if child is None or calls is None or len(calls) != 1:
            continue
        binding = _proven_child(
            saved,
            child,
            namespace=namespace,
            thread=thread,
            tenant=str(tenant_id),
            lineage=owners,
            message=message,
            call=calls[0],
        )
        if binding is None:
            continue
        child_slots = _pending(child)
        child_sends = _tool_dispatches(child)
        if child_sends is None:
            continue
        for ident in ids:
            slot = child_slots.get(ident)
            outer = native.get(ident)
            if (
                slot is not None
                and outer is not None
                and outer[0] == task_id
                and outer[1].value == slot[1].value
                and not _contains_task(child_sends.get(slot[0], []))
            ):
                result[ident] = binding
    return result
