"""Select and materialize the exact root checkpoint used by a chat fork."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import convert_to_messages
from langgraph.channels.delta import DeltaChannel
from langgraph.graph.message import _messages_delta_reducer  # noqa: PLC2701

from app.services.thread_branch_service import without_trailing_assistant_messages
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

type ForkOperation = Literal["edit", "regenerate"]


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="The conversation branch is no longer available")


def _root_id(config: dict[str, Any] | None, thread_id: str) -> str | None:
    keys = (config or {}).get("configurable", {})
    if keys.get("thread_id") == thread_id and keys.get("checkpoint_ns", "") == "":
        value = keys.get("checkpoint_id")
        if isinstance(value, str) and value:
            return value
    return None


def _ancestors(checkpoint_id: str, parents: dict[str, str | None]) -> Iterator[str]:
    seen: set[str] = set()
    current: str | None = checkpoint_id
    while current is not None:
        if current not in parents or current in seen:
            raise _conflict()
        seen.add(current)
        yield current
        current = parents[current]


async def selected_branch_head(
    saver: Any,
    thread_id: str,
    selected: str | None,
    target: str | None,
    expected: str | None,
    *,
    tenant_id: UUID,
) -> str:
    # Finish the generator before asking for delta history: a Postgres iterator
    # holds its connection. Keep just the root ancestry, never subgraph states.
    parents: dict[str, str | None] = {}
    invalid: set[str] = set()
    async for row in saver.alist({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}):
        cid = _root_id(row.config, thread_id)
        if cid is not None:
            if (
                row.metadata.get(CHECKPOINT_TENANT_KEY) not in (None, str(tenant_id))
                or row.checkpoint.get("id") != cid
            ):
                invalid.add(cid)
            parent = _root_id(row.parent_config, thread_id)
            if row.parent_config and parent is None:
                raise _conflict()
            parents[cid] = parent
    if (target is not None and target not in parents) or (
        selected is not None and selected not in parents
    ):
        raise _conflict()
    parent_ids = set(parents.values())
    for leaf in parents:  # saver lists latest first, matching the branch picker
        if leaf in parent_ids:
            continue
        chain = set(_ancestors(leaf, parents))
        if selected is None or selected in chain:
            if (
                invalid.intersection(chain)
                or (expected is not None and expected != leaf)
                or (target is not None and target not in chain)
            ):
                raise _conflict()
            return leaf
    raise _conflict()


def _checkpoint_messages(value: Any) -> list[BaseMessage]:
    """Older full snapshots may contain nested containers of BaseMessage."""
    if not isinstance(value, list):
        raise _conflict()
    pending: list[Any] = list(reversed(value))
    messages: list[BaseMessage] = []
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(reversed(item))
        elif isinstance(item, BaseMessage):
            messages.append(item)
        elif isinstance(item, dict):
            messages.extend(convert_to_messages([item]))
        else:
            raise _conflict()
    return messages


async def load_fork_checkpoint(
    saver: Any,
    *,
    thread_id: str,
    tenant_id: UUID,
    selected: str | None,
    target: str,
    expected: str | None,
    operation: ForkOperation,
) -> tuple[str, list[BaseMessage]]:
    head = await selected_branch_head(
        saver, thread_id, selected, target, expected, tenant_id=tenant_id
    )
    config = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": target}
    }
    row = await saver.aget_tuple(config)
    if (
        row is None
        or _root_id(row.config, thread_id) != target
        or row.checkpoint.get("id") != target
        or row.metadata.get(CHECKPOINT_TENANT_KEY) not in (None, str(tenant_id))
    ):
        raise _conflict()
    checkpoint = row.checkpoint
    values = checkpoint["channel_values"]
    # Use the same channel and reducer as DeepAgentState. Replaying by appending
    # writes would resurrect deleted turns and duplicate same-ID replacements.
    channel = DeltaChannel(lambda base, writes: _messages_delta_reducer(base, list(writes)))
    try:
        if "messages" in values:
            state = channel.from_checkpoint(values["messages"])
        elif "messages" in checkpoint["channel_versions"]:
            histories = await saver.aget_delta_channel_history(config=config, channels=["messages"])
            history = histories.get("messages")
            if not history or (history.get("seed") is None and not history["writes"]):
                raise _conflict()
            state = channel.from_checkpoint(history.get("seed"))
            state.replay_writes(history["writes"])
        else:
            state = channel.from_checkpoint(None)  # pre-input checkpoint of the first human turn
        messages = _checkpoint_messages(state.get())
    except (KeyError, TypeError, ValueError, NotImplementedError) as exc:
        raise _conflict() from exc

    pending_calls: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            pending_calls.update(
                call_id for call in message.tool_calls if (call_id := call.get("id"))
            )
        elif isinstance(message, ToolMessage):
            pending_calls.discard(message.tool_call_id)
    if pending_calls:
        raise HTTPException(status_code=409, detail="The checkpoint has an unresolved tool call")

    if operation == "regenerate":
        messages = without_trailing_assistant_messages(messages)
        if not any(isinstance(message, HumanMessage) for message in messages):
            raise HTTPException(
                status_code=422, detail="Regeneration requires an existing user turn"
            )
    return head, messages
