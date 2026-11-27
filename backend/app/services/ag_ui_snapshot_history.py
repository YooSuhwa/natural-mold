"""Read the committed root message channel on the selected conversation branch."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast
from uuid import UUID

from langchain_core.messages import AnyMessage, BaseMessage, convert_to_messages
from langgraph.channels.binop import _get_overwrite
from langgraph.graph.message import _messages_delta_reducer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.services.conversation_protocol_projection import project_todo_policy_values
from app.services.thread_branch_service import _unwrap_delta_snapshot
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

type SnapshotMessage = BaseMessage | dict[str, Any]


@dataclass(frozen=True, slots=True)
class SnapshotMessageRef:
    message: SnapshotMessage
    original_index: int
    checkpoint_id: str | None
    parent_checkpoint_id: str | None
    sibling_checkpoint_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotHistory:
    checkpoint_id: str | None
    messages: tuple[SnapshotMessageRef, ...]
    source: Literal["checkpoint", "retained", "legacy"]
    lineage_checkpoint_ids: tuple[str, ...] = ()
    has_branches: bool = False
    checkpoint_run_ids: dict[str, UUID] = field(default_factory=dict)
    other_branch_run_ids: frozenset[UUID] = frozenset()
    todos: tuple[dict[str, Any], ...] = ()


def _snapshot_todos(value: Any, secret_values: Collection[str]) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        return ()
    from app.agent_runtime.ag_ui_v1_activity_payloads import public_todos

    visible = public_todos({"todos": value})
    if not visible:
        return ()
    redacted = project_and_redact_protocol_data("values", visible, secret_values=secret_values)
    sanitized = public_todos(redacted)
    return tuple(sanitized["todos"]) if sanitized else ()


def _conflict() -> ConflictError:
    return ConflictError("CONVERSATION_HISTORY_INCOMPLETE", "Selected branch is unavailable")


def _lineage(parents: dict[str, str | None], leaf: str) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = leaf
    while current is not None:
        if current not in parents or current in seen:
            raise _conflict()
        seen.add(current)
        chain.append(current)
        current = parents[current]
    return tuple(reversed(chain))


def _select_lineage(parents: dict[str, str | None], selected: str | None) -> tuple[str, ...]:
    """Resolve a selected ancestor to its newest reachable leaf. Input is newest first."""
    if selected is not None and selected not in parents:
        raise _conflict()
    if not parents:
        return ()
    children: defaultdict[str, list[str]] = defaultdict(list)
    for child, parent in parents.items():
        if parent is not None:
            children[parent].append(child)
    reachable: set[str] | None = None
    if selected is not None:
        reachable = {selected}
        stack = [selected]
        while stack:
            for child in children.get(stack.pop(), []):
                if child in reachable:
                    raise _conflict()
                reachable.add(child)
                stack.append(child)
    for checkpoint_id in parents:
        if checkpoint_id not in children and (reachable is None or checkpoint_id in reachable):
            return _lineage(parents, checkpoint_id)
    raise _conflict()


def _message_id(message: SnapshotMessage) -> str | None:
    value = message.id if isinstance(message, BaseMessage) else message.get("id")
    return value if isinstance(value, str) and value else None


def _replace_snapshot(
    refs: Sequence[SnapshotMessageRef],
    messages: Sequence[SnapshotMessage],
    checkpoint_id: str,
    parent_checkpoint_id: str | None,
) -> list[SnapshotMessageRef]:
    """Keep introduction provenance for surviving messages, including moves on compaction."""
    previous: dict[str, SnapshotMessageRef | None] = {}
    for ref in refs:
        if (mid := _message_id(ref.message)) is not None:
            previous[mid] = None if mid in previous else ref
    counts = Counter(_message_id(message) for message in messages)
    result: list[SnapshotMessageRef] = []
    for index, message in enumerate(messages):
        mid = _message_id(message)
        if mid is not None and (counts[mid] > 1 or (mid in previous and previous[mid] is None)):
            result.append(SnapshotMessageRef(message, index, None, None))
            continue
        if index < len(refs) and (refs[index].message is message or refs[index].message == message):
            result.append(refs[index])
            continue
        old = previous.get(mid) if mid is not None else None
        if old is not None and old.message == message:
            result.append(replace(old, original_index=index))
        elif mid is None and any(ref.message == message for ref in refs):
            result.append(SnapshotMessageRef(message, index, None, None))
        else:
            result.append(SnapshotMessageRef(message, index, checkpoint_id, parent_checkpoint_id))
    return result


@dataclass(slots=True)
class _Checkpoint:
    parent: str | None
    run_id: UUID | None
    snapshot: list[AnyMessage] | None
    message_writes: list[Any]
    message_version: Any
    todos: Any


def _root_config(config: dict[str, Any], thread_id: str) -> bool:
    return config.get("thread_id", thread_id) == thread_id and config.get("checkpoint_ns", "") == ""


def _messages(value: Any) -> list[AnyMessage]:
    # Older saver versions can nest batches. Unwrap only outer containers;
    # provider content blocks inside a message must survive intact.
    pending = [_unwrap_delta_snapshot(value)]
    messages: list[BaseMessage] = []
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(reversed(item))
        elif isinstance(item, BaseMessage):
            messages.append(item)
        elif item is not None:
            try:
                messages.extend(convert_to_messages([item]))
            except (TypeError, ValueError, NotImplementedError):
                raise _conflict() from None
    return cast(list[AnyMessage], messages)


def _run_id(metadata: dict[str, Any], tenant_id: UUID | None) -> UUID | None:
    tenant = metadata.get(CHECKPOINT_TENANT_KEY)
    if tenant is not None and tenant != str(tenant_id):
        raise _conflict()
    value = metadata.get("moldy_run_id")
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError as error:
        raise _conflict() from error


async def _root_checkpoints(
    saver: Any, thread_id: str, tenant_id: UUID | None
) -> dict[str, _Checkpoint]:
    # Consume alist before any other pool operation. A postgres saver may hold
    # its connection for the complete iterator. Never retain raw checkpoint
    # state, pending graph inputs, child messages or uncommitted tool results.
    result: dict[str, _Checkpoint] = {}
    async for record in saver.alist(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    ):
        config = record.config["configurable"]
        if not _root_config(config, thread_id):
            continue
        cid = config.get("checkpoint_id")
        parent = (record.parent_config or {}).get("configurable", {})
        if not isinstance(cid, str) or cid in result or not _root_config(parent, thread_id):
            raise _conflict()
        pid = parent.get("checkpoint_id")
        if pid is not None and not isinstance(pid, str):
            raise _conflict()
        checkpoint = record.checkpoint
        values = checkpoint.get("channel_values", {})
        snapshot = _messages(values["messages"]) if values.get("messages") is not None else None
        result[cid] = _Checkpoint(
            pid,
            _run_id(getattr(record, "metadata", {}) or {}, tenant_id),
            snapshot,
            [
                value
                for _, channel, value in (getattr(record, "pending_writes", None) or ())
                if channel == "messages"
            ],
            checkpoint.get("channel_versions", {}).get("messages"),
            values.get("todos"),
        )
    return result


def _at_checkpoint(
    checkpoint: _Checkpoint, parent: _Checkpoint | None, previous: list[AnyMessage]
) -> list[AnyMessage]:
    if checkpoint.snapshot is not None:
        return checkpoint.snapshot
    if checkpoint.message_version is None:
        return []
    if parent is None or not parent.message_writes:
        if parent is None or checkpoint.message_version != parent.message_version:
            # A version advanced without a stored snapshot or contributing
            # writes. Returning the ancestor's transcript could restore a
            # superseded message or bind it to the wrong run.
            raise _conflict()
        return previous
    # A parent's writes become committed at the child. A leaf's own writes
    # are pending (including while waiting for approval) and never appear.
    # Use the pinned runtime's reducer for remove/replace-by-id semantics.
    writes = parent.message_writes
    for index in range(len(writes) - 1, -1, -1):
        overwrite, value = _get_overwrite(writes[index])
        if overwrite:
            previous = _messages(value)
            writes = writes[index + 1 :]
            break
    return (
        _messages_delta_reducer(previous, [_messages(batch) for batch in writes])
        if writes
        else previous
    )


def _leaf_messages(
    checkpoints: dict[str, _Checkpoint], parents: dict[str, str | None], leaf: str
) -> list[AnyMessage]:
    path = _lineage(parents, leaf)
    start = next(
        (i for i in reversed(range(len(path))) if checkpoints[path[i]].snapshot is not None),
        0,
    )
    messages: list[AnyMessage] = []
    for cid in path[start:]:
        checkpoint = checkpoints[cid]
        parent = checkpoints.get(checkpoint.parent) if checkpoint.parent is not None else None
        messages = _at_checkpoint(checkpoint, parent, messages)
    return messages


def _add_siblings(
    refs: list[SnapshotMessageRef],
    checkpoints: dict[str, _Checkpoint],
    parents: dict[str, str | None],
    active: str,
    leaves: list[str],
) -> None:
    # Only disclose opaque leaf IDs at the first divergence from the selected
    # transcript. Oldest first matches Moldy's existing branch picker.
    alternatives: defaultdict[int, list[tuple[SnapshotMessage, str]]] = defaultdict(list)
    for leaf in leaves:
        if leaf == active:
            continue
        try:
            messages = _leaf_messages(checkpoints, parents, leaf)
        except ConflictError:
            continue
        for index, (ref, other) in enumerate(zip(refs, messages, strict=False)):
            if ref.message != other:
                if all(other != known for known, _ in alternatives[index]):
                    alternatives[index].append((other, leaf))
                break
    rank = {leaf: index for index, leaf in enumerate(leaves)}
    for index, variants in alternatives.items():
        ids = [active, *(leaf for _, leaf in variants)]
        refs[index] = replace(
            refs[index],
            sibling_checkpoint_ids=tuple(sorted(ids, key=rank.__getitem__, reverse=True)),
        )


async def _live_history(
    saver: Any,
    thread_id: str,
    selected: str | None,
    *,
    tenant_id: UUID | None = None,
    secret_values: Collection[str] = (),
) -> SnapshotHistory:
    checkpoints = await _root_checkpoints(saver, thread_id, tenant_id)
    parents = {cid: item.parent for cid, item in checkpoints.items()}
    path = _select_lineage(parents, selected)
    if not path:
        return SnapshotHistory(None, (), "legacy")
    refs: list[SnapshotMessageRef] = []
    messages: list[AnyMessage] = []
    parent = None
    for cid in path:
        checkpoint = checkpoints[cid]
        updated = _at_checkpoint(checkpoint, parent, messages)
        if updated is not messages:
            refs = _replace_snapshot(refs, updated, cid, checkpoint.parent)
        messages, parent = updated, checkpoint
    parent_ids = set(parents.values())
    leaves = [cid for cid in checkpoints if cid not in parent_ids]
    if len(leaves) > 1:
        _add_siblings(refs, checkpoints, parents, path[-1], leaves)
    selected_checkpoints = set(path)
    return SnapshotHistory(
        path[-1],
        tuple(refs),
        "checkpoint",
        path,
        len(leaves) > 1,
        {cid: owner for cid in path if (owner := checkpoints[cid].run_id) is not None},
        frozenset(
            item.run_id
            for cid, item in checkpoints.items()
            if cid not in selected_checkpoints and item.run_id is not None
        ),
        _snapshot_todos(checkpoints[path[-1]].todos, secret_values),
    )


async def load_snapshot_history(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    *,
    secret_values: Collection[str] = (),
) -> SnapshotHistory:
    conversation = await require_private_conversation(db, conversation.id, context)
    if conversation.agent_lifecycle_state == "purged":
        from app.services.ag_ui_snapshot_retained import read_retained_snapshot_history

        history = await read_retained_snapshot_history(
            db, conversation, secret_values=secret_values
        )
    else:
        try:
            saver = await loaded_conversation_checkpointer(conversation, context, db=db)
        except RuntimeError as error:
            if not str(error).startswith("Checkpointer not initialized."):
                raise
            # An unavailable saver cannot prove an old conversation was unbranched.
            raise _conflict() from error
        history = await _live_history(
            saver,
            str(conversation.id),
            conversation.active_branch_checkpoint_id,
            tenant_id=context.tenant_id,
            secret_values=secret_values,
        )
    if history.todos and "todos" not in project_todo_policy_values(conversation, {"todos": []}):
        return replace(history, todos=())
    return history
