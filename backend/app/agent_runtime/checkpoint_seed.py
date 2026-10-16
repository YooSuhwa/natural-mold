"""Locked capture of a prepared run's initial checkpoint state (parent Task 10).

One ``aget_tuple`` resolves the root seed (the explicit fork/edit checkpoint or
the thread head at preparation time). When the root seed is paused on an
interrupt raised inside a child graph, the paused child namespaces are derived
exactly — ``<catalog node>:<interrupted task id>`` from the seed's own INTERRUPT
writes — and captured too, so a resume continues the child instead of
replaying it. Delta-channel history each captured tuple needs is read in the
same preparation window; execution never reads another historical checkpoint.

Reads return a fresh copy with this lease's own writes on that tuple layered
on top using the upstream ``(task_id, idx)`` replace rules.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    DeltaChannelHistory,
    PendingWrite,
)

_INTERRUPT = "__interrupt__"
_MAX_DEPTH = 4


@dataclass(frozen=True, slots=True)
class SeedEntry:
    saved: CheckpointTuple = field(repr=False)
    delta_history: Mapping[str, DeltaChannelHistory] = field(repr=False)

    @property
    def checkpoint_id(self) -> str:
        return str(self.saved.config.get("configurable", {}).get("checkpoint_id"))

    def read(self, own_writes: Sequence[tuple[str, Sequence[tuple[str, Any]]]]) -> CheckpointTuple:
        fresh = copy.deepcopy(self.saved)
        keyed: list[tuple[tuple[str, int], PendingWrite]] = []
        positions: dict[str, int] = {}
        for task_id, channel, value in fresh.pending_writes or []:
            index = WRITES_IDX_MAP.get(channel)
            if index is None:
                index = positions.get(task_id, 0)
                positions[task_id] = index + 1
            keyed.append(((task_id, index), (task_id, channel, value)))
        for task_id, writes in own_writes:
            replace = all(channel in WRITES_IDX_MAP for channel, _ in writes)
            for position, (channel, value) in enumerate(writes):
                key = (task_id, WRITES_IDX_MAP.get(channel, position))
                write: PendingWrite = (task_id, channel, copy.deepcopy(value))
                existing = next((i for i, (k, _w) in enumerate(keyed) if k == key), None)
                if existing is None:
                    keyed.append((key, write))
                elif replace:
                    keyed[existing] = (key, write)
        return fresh._replace(pending_writes=[write for _key, write in keyed])


@dataclass(frozen=True, slots=True)
class CheckpointSeed:
    thread_id: str
    entries: Mapping[str, SeedEntry] = field(repr=False)

    @property
    def checkpoint_id(self) -> str | None:
        root = self.entries.get("")
        return None if root is None else root.checkpoint_id


async def _capture(
    upstream: BaseCheckpointSaver[Any],
    config: Mapping[str, Any],
    delta_channels: Sequence[str],
) -> SeedEntry | None:
    saved = await upstream.aget_tuple({"configurable": dict(config)})
    if saved is None:
        return None
    history: Mapping[str, DeltaChannelHistory] = {}
    if delta_channels:
        history = await upstream.aget_delta_channel_history(
            config=saved.config, channels=list(delta_channels)
        )
    return SeedEntry(copy.deepcopy(saved), copy.deepcopy(dict(history)))


def _interrupted_task_ids(entry: SeedEntry) -> list[str]:
    ids = {
        task_id for task_id, channel, _ in entry.saved.pending_writes or [] if channel == _INTERRUPT
    }
    return sorted(ids)


async def capture_checkpoint_seed(
    upstream: BaseCheckpointSaver[Any],
    *,
    thread_id: str,
    checkpoint_id: str | None,
    delta_channels: Sequence[str],
    child_node_names: Iterable[str] = (),
) -> CheckpointSeed:
    root_config: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        root_config["checkpoint_id"] = checkpoint_id
    entries: dict[str, SeedEntry] = {}
    root = await _capture(upstream, root_config, delta_channels)
    frontier: list[tuple[str, SeedEntry, int]] = []
    if root is not None:
        entries[""] = root
        frontier.append(("", root, 0))
    names = sorted(set(child_node_names))
    while frontier:
        parent_ns, entry, depth = frontier.pop(0)
        if depth >= _MAX_DEPTH:
            continue
        for task_id in _interrupted_task_ids(entry):
            for name in names:
                segment = f"{name}:{task_id}"
                namespace = f"{parent_ns}|{segment}" if parent_ns else segment
                config = {"thread_id": thread_id, "checkpoint_ns": namespace}
                child = await _capture(upstream, config, delta_channels)
                if child is not None:
                    entries[namespace] = child
                    frontier.append((namespace, child, depth + 1))
    return CheckpointSeed(thread_id, entries)
