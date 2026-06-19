"""Thread branch service — LangGraph checkpoint timeline as a message tree.

LangGraph stores every state transition as a checkpoint, so the full message
history of a thread is recoverable by walking the checkpoint timeline. When a
user edits or regenerates a message we re-invoke from an earlier checkpoint;
LangGraph creates a new branch (sibling subtree) instead of appending to the
tip. This service surfaces that tree to the API so the frontend can render
``<1/2>`` style branch pickers.

M-CHAT1b HOTFIX (2026-05-01):
Previously ``_build_tree_from_checkpoints`` emitted *every* leaf branch's
messages as nodes, causing the UI to show duplicated message bubbles after
an edit (e.g. [user-old, ai-old, user-new, ai-new] all on screen at once).

The fix:
- ``nodes`` now contains *only* the active branch's root→leaf message chain
  (one bubble per position).
- ``branches_by_message[mid]`` maps each branchable message id to a list of
  ``BranchSibling(message_id, checkpoint_id)`` tuples — including the active
  one itself — so the frontend's ``◀ 1/N ▶`` picker can switch branches by
  posting the chosen sibling's checkpoint_id to ``/switch-branch``.

The active branch is selected via:
1. ``active_checkpoint_id`` argument (set from
   ``Conversation.active_branch_checkpoint_id`` when the user has explicitly
   chosen a branch via the picker), or
2. fallback: the most recent leaf (``checkpoints[0]`` — ``alist`` returns
   newest-first).

Public surface:

- ``rewind_to_checkpoint_before_message(...)`` — find the checkpoint id whose
  state has exactly N messages (so re-invoking from it forks off message N).
- ``build_message_tree(...)`` — collect every message from every leaf
  checkpoint, decide which branch is active, and emit ``(message,
  parent_message_id)`` tuples for the active chain plus sibling metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


@dataclass
class _CheckpointSlim:
    """Trimmed view of a CheckpointTuple — only what the tree builder needs."""

    checkpoint_id: str
    parent_checkpoint_id: str | None
    messages: list[BaseMessage]


@dataclass
class _CheckpointHeader:
    """Checkpoint metadata collected without materializing DeltaChannel messages."""

    checkpoint_id: str
    parent_checkpoint_id: str | None
    messages: Any
    versions: dict[str, Any]


@dataclass
class BranchSibling:
    """One sibling at a branch point — message id + the checkpoint to switch to."""

    message_id: str
    checkpoint_id: str


@dataclass
class MessageTreeNode:
    """One node in the active branch's message chain."""

    message: BaseMessage
    parent_id: str | None
    # checkpoint that introduced this message — used by `setMessages` callback
    # on the frontend to flip the active branch.
    introduced_by_checkpoint_id: str
    # 0-based index of *this* (active) message within its sibling list, when
    # there are siblings. ``None`` for messages with no siblings. Frontend uses
    # this directly to render ``<N/M>`` so it doesn't need to indexOf the
    # active message id.
    branch_index: int | None = None
    # Total siblings at this position (>= 2 when ``branch_index`` is set,
    # otherwise ``None``).
    branch_total: int | None = None


@dataclass
class MessageTree:
    nodes: list[MessageTreeNode]
    active_tip_message_id: str | None
    active_checkpoint_id: str | None
    # message_id → list of all sibling BranchSiblings at this branch point
    # (including the active one itself, so frontend can compute current_index).
    # Populated only for messages that actually have siblings.
    branches_by_message: dict[str, list[BranchSibling]] = field(default_factory=dict)


async def _collect_checkpoints(
    checkpointer: Any,
    thread_id: str,
) -> list[_CheckpointSlim]:
    """Stream all checkpoints for a thread, trimmed to id/parent/messages.

    langgraph 1.2+ stores `messages` as a `DeltaChannel`: a non-snapshot
    checkpoint omits the channel from `channel_values` and stores only the
    write deltas. Reading `channel_values["messages"]` therefore returns
    `None` for most checkpoints. We materialize the accumulated value by
    calling the saver's `aget_delta_channel_history` and replaying writes
    onto the seed snapshot — the same logic LangGraph runs during graph
    resume (`pregel/_checkpoint.py:achannels_from_checkpoint`).

    Two-phase to avoid checkpointer connection deadlock: phase 1 fully
    consumes `alist` (its async generator holds a postgres connection),
    phase 2 materializes deltas via separate connections. Doing the
    materialize call inside the `alist` loop deadlocks the langgraph
    postgres pool — every nested `aget_delta_channel_history` request
    waits for the same connection that `alist` is holding.
    """

    raw = await _collect_checkpoint_headers(checkpointer, thread_id)

    # Phase 2: materialize DeltaChannel messages for checkpoints that need it.
    out: list[_CheckpointSlim] = []
    for header in raw:
        msgs = await _messages_for_header(checkpointer, thread_id, header)
        out.append(
            _CheckpointSlim(
                checkpoint_id=header.checkpoint_id,
                parent_checkpoint_id=header.parent_checkpoint_id,
                messages=list(msgs or []),
            )
        )
    return out


async def _collect_checkpoint_headers(
    checkpointer: Any,
    thread_id: str,
) -> list[_CheckpointHeader]:
    """Collect checkpoint ids/parents without replaying message histories."""

    config = {"configurable": {"thread_id": thread_id}}
    raw: list[_CheckpointHeader] = []
    async for ct in checkpointer.alist(config):
        cfg = ct.config or {}
        cid = cfg.get("configurable", {}).get("checkpoint_id")
        if cid is None:
            continue
        parent_cfg = ct.parent_config or {}
        pid = parent_cfg.get("configurable", {}).get("checkpoint_id")
        ckpt = ct.checkpoint or {}
        cv = ckpt.get("channel_values", {})
        raw.append(
            _CheckpointHeader(
                checkpoint_id=cid,
                parent_checkpoint_id=pid,
                messages=cv.get("messages"),
                versions=ckpt.get("channel_versions") or {},
            )
        )
    return raw


async def checkpoint_exists(checkpointer: Any, thread_id: str, checkpoint_id: str) -> bool:
    """Return whether a checkpoint belongs to a thread without materializing messages."""

    async for ct in checkpointer.alist({"configurable": {"thread_id": thread_id}}):
        cfg = ct.config or {}
        if cfg.get("configurable", {}).get("checkpoint_id") == checkpoint_id:
            return True
    return False


async def _messages_for_header(
    checkpointer: Any,
    thread_id: str,
    header: _CheckpointHeader,
) -> list[BaseMessage]:
    msgs = header.messages
    if msgs is None and "messages" in header.versions:
        msgs = await materialize_messages_at_checkpoint(
            checkpointer, thread_id, header.checkpoint_id
        )
    return list(msgs or [])


async def materialize_messages_at_checkpoint(
    checkpointer: Any,
    thread_id: str,
    checkpoint_id: str,
) -> list[BaseMessage]:
    """Reconstruct the `messages` list at `checkpoint_id` via DeltaChannel replay.

    Mirrors `DeltaChannel.replay_writes`: start from the snapshot seed, walk
    writes oldest-to-newest, and when an `Overwrite` is seen reset the
    accumulator to its value (fork-edit emits these to truncate ancestor
    writes). Non-`Overwrite` writes append.

    Falls back to `channel_values["messages"]` when the saver predates
    DeltaChannel (e.g. the test fake).
    """

    from langgraph.checkpoint.serde.types import _DeltaSnapshot
    from langgraph.types import Overwrite

    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    try:
        histories = await checkpointer.aget_delta_channel_history(config=cfg, channels=["messages"])
    except (AttributeError, NotImplementedError):
        # Pre-DeltaChannel saver (테스트 fake) — fall back to channel_values.
        tup = await checkpointer.aget_tuple(cfg)
        if tup is None:
            return []
        cv_msgs = (tup.checkpoint or {}).get("channel_values", {}).get("messages")
        return list(cv_msgs or [])
    except Exception:  # noqa: BLE001
        logger.warning(
            "aget_delta_channel_history failed for checkpoint %s", checkpoint_id, exc_info=True
        )
        return []
    history = histories.get("messages")
    if history is None:
        return []
    seed = history.get("seed")
    if isinstance(seed, _DeltaSnapshot):
        base: list[BaseMessage] = list(seed.value or [])
    elif isinstance(seed, list):
        base = list(seed)
    else:
        base = []
    for _, _, batch in history.get("writes", []):
        if isinstance(batch, Overwrite):
            base = list(batch.value or [])
        elif isinstance(batch, list):
            base.extend(batch)
        elif batch is not None:
            base.append(batch)
    return base


async def build_fork_overwrite_input(
    checkpointer: Any,
    thread_id: str,
    checkpoint_id: str | None,
    *,
    append: list[BaseMessage] | None = None,
    drop_trailing_assistant: bool = False,
) -> dict[str, Any]:
    """Build the agent input dict for fork-edit / regenerate.

    langgraph 1.2 DeltaChannel replays ancestor `messages` writes onto any
    forked checkpoint — without explicit truncation the resulting branch
    inherits messages we meant to replace. Wrapping the pre-target state in
    ``Overwrite`` resets the channel; the agent then runs from that exact
    state and produces a clean leaf.
    """

    from langgraph.types import Overwrite

    pre_msgs: list[BaseMessage] = []
    if checkpoint_id is not None:
        try:
            pre_msgs = await materialize_messages_at_checkpoint(
                checkpointer, thread_id, checkpoint_id
            )
        except Exception:  # noqa: BLE001
            pre_msgs = []
    if drop_trailing_assistant:
        pre_msgs = without_trailing_assistant_messages(pre_msgs)
    value: list[BaseMessage] = [*pre_msgs, *(append or [])]
    return {"messages": Overwrite(value=value)}


def without_trailing_assistant_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    trimmed = list(messages)
    while trimmed and _is_assistant_message(trimmed[-1]):
        trimmed.pop()
    return trimmed


def _is_assistant_message(message: BaseMessage) -> bool:
    return getattr(message, "type", None) == "ai"


def _is_leaf(checkpoint_id: str, all_parent_ids: set[str]) -> bool:
    return checkpoint_id not in all_parent_ids


def _message_id(msg: BaseMessage, fallback_idx: int) -> str:
    raw = getattr(msg, "id", None)
    if raw:
        return str(raw)
    # LangChain occasionally emits messages without ids (system/tool stubs).
    # Fall back to a deterministic synthetic id derived from index.
    return f"synthetic-{fallback_idx}"


def _is_synthetic_id(mid: str) -> bool:
    """Synthetic id 는 ``synthetic-{idx}`` 형태. fork-edit 같은 분기에서
    LangChain HumanMessage(id=None) 가 분기마다 같은 synthetic id 로 나오기
    때문에 sibling 비교 시 checkpoint 까지 함께 봐야 한다. 진짜 langchain
    id (e.g. ``lc_run-...``) 는 메시지 단위로 유니크해 id 만으로 dedup."""

    return mid.startswith("synthetic-")


def _resolve_active_leaf(
    checkpoints: list[_CheckpointSlim],
    leaves: list[_CheckpointSlim],
    active_checkpoint_id: str | None,
) -> _CheckpointSlim:
    """Pick which leaf the active branch chain should be sourced from.

    1. If ``active_checkpoint_id`` matches a leaf, use it directly.
    2. If it matches a non-leaf checkpoint, walk forward to the leaf that
       descends from it (so the user's chosen branch's *full* messages render).
    3. Otherwise, fall back to the most recent leaf (``leaves[0]`` — alist is
       DESC).
    """

    if active_checkpoint_id:
        # Direct hit on a leaf.
        for leaf in leaves:
            if leaf.checkpoint_id == active_checkpoint_id:
                return leaf
        # Otherwise: find a leaf whose ancestor chain contains it.
        by_id = {c.checkpoint_id: c for c in checkpoints}
        for leaf in leaves:
            cur: _CheckpointSlim | None = leaf
            while cur is not None:
                if cur.checkpoint_id == active_checkpoint_id:
                    return leaf
                if cur.parent_checkpoint_id is None:
                    break
                cur = by_id.get(cur.parent_checkpoint_id)

    # Fallback — newest leaf wins.
    return leaves[0]


def _build_leaf_chain(
    leaf: _CheckpointSlim,
    by_id: dict[str, _CheckpointSlim],
) -> list[_CheckpointSlim]:
    """Walk root→leaf and return the ordered chain of checkpoints.

    Pulled out so callers can cache the chain for reuse (P0-D). The previous
    inline walks rebuilt the chain inside every ``_checkpoint_for_message_in_leaf``
    call, giving O(L×D×M) overall.
    """

    chain: list[_CheckpointSlim] = []
    cur: _CheckpointSlim | None = leaf
    while cur is not None:
        chain.append(cur)
        if cur.parent_checkpoint_id is None:
            break
        cur = by_id.get(cur.parent_checkpoint_id)
    chain.reverse()
    return chain


def _checkpoint_for_message_in_chain(
    chain: list[_CheckpointSlim],
    target_msg_id: str,
    target_idx: int,
    leaf_id: str,
) -> str:
    """Find the earliest checkpoint in ``chain`` that introduced
    ``target_msg_id`` at ``target_idx``. Falls back to ``leaf_id`` if none
    matches (synthetic ids etc.).
    """

    for ck in chain:
        if (
            target_idx < len(ck.messages)
            and _message_id(ck.messages[target_idx], target_idx) == target_msg_id
        ):
            return ck.checkpoint_id
    return leaf_id


def _checkpoint_for_message_in_leaf(
    leaf: _CheckpointSlim,
    by_id: dict[str, _CheckpointSlim],
    target_msg_id: str,
    target_idx: int,
) -> str:
    """Backward-compat wrapper — used by tests / external callers that pass a
    leaf instead of a pre-built chain.
    """

    chain = _build_leaf_chain(leaf, by_id)
    return _checkpoint_for_message_in_chain(chain, target_msg_id, target_idx, leaf.checkpoint_id)


def _build_tree_from_checkpoints(
    checkpoints: list[_CheckpointSlim],
    active_checkpoint_id: str | None = None,
) -> MessageTree:
    """Build a tree where ``nodes`` is the active branch's chain only.

    Sibling branches at any divergence point are recorded in
    ``branches_by_message`` so the picker can render ``◀ N/M ▶``.
    """

    if not checkpoints:
        return MessageTree(nodes=[], active_tip_message_id=None, active_checkpoint_id=None)

    by_id: dict[str, _CheckpointSlim] = {c.checkpoint_id: c for c in checkpoints}
    parent_ids: set[str] = {c.parent_checkpoint_id for c in checkpoints if c.parent_checkpoint_id}

    leaves = [c for c in checkpoints if _is_leaf(c.checkpoint_id, parent_ids)]
    if not leaves:
        # Defensive — every checkpoint has a child (cycle?). Use newest.
        leaves = [checkpoints[0]]
    checkpoint_rank_by_id = {
        checkpoint.checkpoint_id: index for index, checkpoint in enumerate(checkpoints)
    }

    active = _resolve_active_leaf(checkpoints, leaves, active_checkpoint_id)

    # P0-D: pre-build root→leaf chain for every leaf once. Both the active-chain
    # walk and the per-message sibling discovery hit these chains, so caching
    # cuts the worst case from O(L × D × M) ancestor walks down to O(L × D)
    # build + O(M × L) lookups.
    chains_by_leaf: dict[str, list[_CheckpointSlim]] = {
        leaf.checkpoint_id: _build_leaf_chain(leaf, by_id) for leaf in leaves
    }
    active_chain = chains_by_leaf[active.checkpoint_id]

    # ---------- Active chain → nodes ----------
    nodes: list[MessageTreeNode] = []
    active_msg_ids: list[str] = []
    prev_id: str | None = None
    for idx, msg in enumerate(active.messages):
        mid = _message_id(msg, idx)
        ck_for_msg = _checkpoint_for_message_in_chain(active_chain, mid, idx, active.checkpoint_id)
        nodes.append(
            MessageTreeNode(
                message=msg,
                parent_id=prev_id,
                introduced_by_checkpoint_id=ck_for_msg,
            )
        )
        active_msg_ids.append(mid)
        prev_id = mid

    # ---------- Sibling discovery across other leaves ----------
    # For each idx in active_msg_ids, gather alternative messages from sibling
    # leaves. Two messages are siblings iff:
    #   - they sit at the same idx
    #   - they share the same parent message id (active_msg_ids[idx-1] for
    #     idx>0, or None for idx=0)
    #   - they have different ids
    branches_by_message: dict[str, list[BranchSibling]] = {}

    expected_parent_key: tuple[str, str] | None = None

    for idx, active_mid in enumerate(active_msg_ids):
        if idx > 0:
            expected_parent_key = _message_branch_key(
                active.messages[idx - 1],
                idx - 1,
                nodes[idx - 1].introduced_by_checkpoint_id,
            )

        seen_keys: set[tuple[str, str]] = set()
        siblings: list[BranchSibling] = []

        active_ck = nodes[idx].introduced_by_checkpoint_id
        siblings.append(BranchSibling(message_id=active_mid, checkpoint_id=active_ck))
        seen_keys.add(_message_branch_key(active.messages[idx], idx, active_ck))

        for leaf in leaves:
            if leaf.checkpoint_id == active.checkpoint_id:
                continue
            msgs = leaf.messages
            if idx >= len(msgs):
                continue
            this_mid = _message_id(msgs[idx], idx)
            # Verify same parent — pair-compared (msg_id, introducing_ck).
            this_parent_key: tuple[str, str] | None = None
            if idx > 0:
                parent_mid = _message_id(msgs[idx - 1], idx - 1)
                parent_ck = _checkpoint_for_message_in_chain(
                    chains_by_leaf[leaf.checkpoint_id],
                    parent_mid,
                    idx - 1,
                    leaf.checkpoint_id,
                )
                this_parent_key = _message_branch_key(msgs[idx - 1], idx - 1, parent_ck)
            if this_parent_key != expected_parent_key:
                continue
            sibling_ck = _checkpoint_for_message_in_chain(
                chains_by_leaf[leaf.checkpoint_id],
                this_mid,
                idx,
                leaf.checkpoint_id,
            )
            key = _message_branch_key(msgs[idx], idx, sibling_ck)
            if key in seen_keys:
                continue
            siblings.append(BranchSibling(message_id=this_mid, checkpoint_id=sibling_ck))
            seen_keys.add(key)

        if len(siblings) >= 2:
            siblings.sort(
                key=lambda s: (-checkpoint_rank_by_id.get(s.checkpoint_id, -1), s.checkpoint_id)
            )
            branches_by_message[active_mid] = siblings

            # Stamp the active node with its position in the sibling list so
            # the frontend gets ``branch_index/branch_total`` for free —
            # avoids a second indexOf round-trip and the index-bug it caused.
            active_pos = next(
                (i for i, s in enumerate(siblings) if s.checkpoint_id == active_ck),
                0,
            )
            nodes[idx] = MessageTreeNode(
                message=nodes[idx].message,
                parent_id=nodes[idx].parent_id,
                introduced_by_checkpoint_id=nodes[idx].introduced_by_checkpoint_id,
                branch_index=active_pos,
                branch_total=len(siblings),
            )

    active_tip_msg_id = active_msg_ids[-1] if active_msg_ids else None

    return MessageTree(
        nodes=nodes,
        active_tip_message_id=active_tip_msg_id,
        active_checkpoint_id=active.checkpoint_id,
        branches_by_message=branches_by_message,
    )


def _message_fingerprint(msg: BaseMessage) -> tuple[str, str, str | None]:
    return (
        str(getattr(msg, "type", msg.__class__.__name__)),
        repr(getattr(msg, "content", "")),
        getattr(msg, "name", None),
    )


def _message_branch_key(msg: BaseMessage, idx: int, checkpoint_id: str) -> tuple[str, str]:
    mid = _message_id(msg, idx)
    if _is_synthetic_id(mid):
        return (mid, checkpoint_id)
    return (mid, repr(_message_fingerprint(msg)))


def _resolve_active_leaf_from_parent_map(
    leaves: list[_CheckpointSlim],
    parent_by_id: dict[str, str | None],
    active_checkpoint_id: str | None,
) -> _CheckpointSlim:
    if active_checkpoint_id:
        for leaf in leaves:
            if leaf.checkpoint_id == active_checkpoint_id:
                return leaf
        for leaf in leaves:
            cur: str | None = leaf.checkpoint_id
            while cur is not None:
                if cur == active_checkpoint_id:
                    return leaf
                cur = parent_by_id.get(cur)
    return leaves[0]


def _build_tree_from_leaf_checkpoints(
    leaves: list[_CheckpointSlim],
    parent_by_id: dict[str, str | None],
    active_checkpoint_id: str | None = None,
) -> MessageTree:
    """Build the active branch tree from materialized leaves only.

    Listing messages only needs leaf states: each leaf already contains the full
    message chain for that branch. Using leaf checkpoint ids for branch
    switching is enough because `/switch-branch` resolves any selected tip as
    the active branch. The full checkpoint materialization path remains
    available for rewind/regenerate, where exact pre-message checkpoints matter.
    """

    if not leaves:
        return MessageTree(nodes=[], active_tip_message_id=None, active_checkpoint_id=None)

    active = _resolve_active_leaf_from_parent_map(leaves, parent_by_id, active_checkpoint_id)
    checkpoint_rank_by_id = {leaf.checkpoint_id: index for index, leaf in enumerate(leaves)}

    nodes: list[MessageTreeNode] = []
    active_msg_ids: list[str] = []
    prev_id: str | None = None
    for idx, msg in enumerate(active.messages):
        mid = _message_id(msg, idx)
        nodes.append(
            MessageTreeNode(
                message=msg,
                parent_id=prev_id,
                introduced_by_checkpoint_id=active.checkpoint_id,
            )
        )
        active_msg_ids.append(mid)
        prev_id = mid

    def sibling_key(msg: BaseMessage, idx: int) -> tuple[str, str]:
        mid = _message_id(msg, idx)
        return (mid, repr(_message_fingerprint(msg)))

    branches_by_message: dict[str, list[BranchSibling]] = {}

    for idx, active_mid in enumerate(active_msg_ids):
        expected_parent_key: tuple[str, str] | None = None
        if idx > 0:
            expected_parent_key = sibling_key(active.messages[idx - 1], idx - 1)

        seen_keys: set[tuple[str, str]] = set()
        siblings: list[BranchSibling] = [
            BranchSibling(message_id=active_mid, checkpoint_id=active.checkpoint_id)
        ]
        seen_keys.add(sibling_key(active.messages[idx], idx))

        for leaf in leaves:
            if leaf.checkpoint_id == active.checkpoint_id or idx >= len(leaf.messages):
                continue
            if idx > 0:
                parent_key = sibling_key(leaf.messages[idx - 1], idx - 1)
                if parent_key != expected_parent_key:
                    continue
            key = sibling_key(leaf.messages[idx], idx)
            if key in seen_keys:
                continue
            siblings.append(
                BranchSibling(
                    message_id=_message_id(leaf.messages[idx], idx),
                    checkpoint_id=leaf.checkpoint_id,
                )
            )
            seen_keys.add(key)

        if len(siblings) >= 2:
            siblings.sort(
                key=lambda s: (-checkpoint_rank_by_id.get(s.checkpoint_id, -1), s.checkpoint_id)
            )
            branches_by_message[active_mid] = siblings
            active_pos = next(
                (i for i, s in enumerate(siblings) if s.checkpoint_id == active.checkpoint_id),
                0,
            )
            nodes[idx] = MessageTreeNode(
                message=nodes[idx].message,
                parent_id=nodes[idx].parent_id,
                introduced_by_checkpoint_id=nodes[idx].introduced_by_checkpoint_id,
                branch_index=active_pos,
                branch_total=len(siblings),
            )

    return MessageTree(
        nodes=nodes,
        active_tip_message_id=active_msg_ids[-1] if active_msg_ids else None,
        active_checkpoint_id=active.checkpoint_id,
        branches_by_message=branches_by_message,
    )


async def _collect_leaf_checkpoints(
    checkpointer: Any,
    thread_id: str,
) -> tuple[list[_CheckpointSlim], dict[str, str | None]]:
    headers = await _collect_checkpoint_headers(checkpointer, thread_id)
    parent_by_id = {h.checkpoint_id: h.parent_checkpoint_id for h in headers}
    parent_ids = {h.parent_checkpoint_id for h in headers if h.parent_checkpoint_id}
    leaf_headers = [h for h in headers if h.checkpoint_id not in parent_ids]
    if not leaf_headers and headers:
        leaf_headers = [headers[0]]

    leaves: list[_CheckpointSlim] = []
    for header in leaf_headers:
        leaves.append(
            _CheckpointSlim(
                checkpoint_id=header.checkpoint_id,
                parent_checkpoint_id=header.parent_checkpoint_id,
                messages=await _messages_for_header(checkpointer, thread_id, header),
            )
        )
    return leaves, parent_by_id


async def build_message_tree(
    checkpointer: Any,
    thread_id: str,
    active_checkpoint_id: str | None = None,
) -> MessageTree:
    """Public entry: walk checkpoints and produce the active-branch tree.

    Pass ``active_checkpoint_id`` (typically
    ``Conversation.active_branch_checkpoint_id``) to render a specific user-
    chosen branch; ``None`` falls back to the newest leaf.
    """

    leaves, parent_by_id = await _collect_leaf_checkpoints(checkpointer, thread_id)
    return _build_tree_from_leaf_checkpoints(leaves, parent_by_id, active_checkpoint_id)


async def build_message_tree_full(
    checkpointer: Any,
    thread_id: str,
    active_checkpoint_id: str | None = None,
) -> MessageTree:
    """Compatibility path that materializes every checkpoint."""

    checkpoints = await _collect_checkpoints(checkpointer, thread_id)
    return _build_tree_from_checkpoints(checkpoints, active_checkpoint_id)


async def rewind_to_checkpoint_before_message(
    checkpointer: Any,
    thread_id: str,
    target_message_id: str,
) -> str | None:
    """Find the checkpoint to invoke from so a new turn replaces ``target_message_id``.

    Looks for the latest checkpoint whose state ends *just before*
    ``target_message_id`` — i.e. its messages list does NOT contain it but its
    children do. Re-invoking with that checkpoint forks a sibling of
    ``target_message_id``.

    Returns ``None`` if the message isn't found in any checkpoint (e.g. caller
    passed a synthetic id or stale state).
    """

    checkpoints = await _collect_checkpoints(checkpointer, thread_id)
    if not checkpoints:
        return None

    by_id = {c.checkpoint_id: c for c in checkpoints}

    # Find a checkpoint that contains target_message_id and locate its parent.
    for ck in checkpoints:
        for idx, msg in enumerate(ck.messages):
            if _message_id(msg, idx) == target_message_id:
                # The checkpoint *before* target_message_id was added is the
                # one we need to invoke from. We walk parent chain back until
                # the parent's messages length < idx + 1 OR the parent doesn't
                # contain target_message_id at this index.
                parent_id = ck.parent_checkpoint_id
                while parent_id is not None:
                    parent = by_id.get(parent_id)
                    if parent is None:
                        break
                    has_target = (
                        idx < len(parent.messages)
                        and _message_id(parent.messages[idx], idx) == target_message_id
                    )
                    if not has_target:
                        return parent.checkpoint_id
                    parent_id = parent.parent_checkpoint_id
                # No parent found — target was the very first message; fork
                # from the empty root by returning None (caller treats None as
                # "start fresh from an empty thread state").
                return None

    return None


def find_checkpoint_for_message(tree: MessageTree, message_id: str) -> str | None:
    """Look up the checkpoint that introduced ``message_id`` in ``tree``."""

    for node in tree.nodes:
        mid = _message_id(node.message, 0)
        if mid == message_id or getattr(node.message, "id", None) == message_id:
            return node.introduced_by_checkpoint_id
    return None
