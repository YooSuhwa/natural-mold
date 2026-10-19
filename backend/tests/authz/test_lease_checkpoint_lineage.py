"""Lease-bound checkpoint saver: one seed read, same-lineage descendants only."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime.checkpoint_seed import capture_checkpoint_seed
from app.agent_runtime.lease_checkpoint_namespace import (
    CheckpointLineageDeniedError,
    LeaseNamespaceAuthority,
)
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver

THREAD = "thread-lineage"
RUN = "run-lineage"


class CountingSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.write_paths: list[str] = []

    async def aget_tuple(self, config: Any) -> Any:
        self.reads.append(str(config["configurable"].get("checkpoint_id")))
        return await super().aget_tuple(config)

    async def aput_writes(
        self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = ""
    ) -> None:
        self.write_paths.append(task_path)
        await super().aput_writes(config, writes, task_id, task_path)


def _config(checkpoint_id: str | None = None, ns: str = "", run: str = RUN) -> Any:
    configurable: dict[str, Any] = {"thread_id": THREAD, "checkpoint_ns": ns, "moldy_run_id": run}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


async def _put(saver: BaseCheckpointSaver[Any], config: Any, value: str) -> Any:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"value": value}
    checkpoint["channel_versions"] = {"value": saver.get_next_version(None, None)}
    return await saver.aput(config, checkpoint, {"step": 1}, checkpoint["channel_versions"])


async def _lease(upstream: CountingSaver, checkpoint_id: str | None = None) -> LeaseCheckpointSaver:
    seed = await capture_checkpoint_seed(
        upstream, thread_id=THREAD, checkpoint_id=checkpoint_id, delta_channels=()
    )
    return LeaseCheckpointSaver(
        upstream, seed=seed, run_id=RUN, namespaces=LeaseNamespaceAuthority({"tools"})
    )


@pytest.mark.asyncio
async def test_single_seed_read_ignores_later_upstream_head() -> None:
    upstream = CountingSaver()
    head = await _put(upstream, _config(), "seed")
    saver = await _lease(upstream)
    assert len(upstream.reads) == 1
    # Another writer advances the thread head after preparation.
    await _put(upstream, head, "foreign")

    seen = await saver.aget_tuple(_config())
    again = await saver.aget_tuple(_config(head["configurable"]["checkpoint_id"]))

    assert seen is not None and seen.checkpoint["channel_values"] == {"value": "seed"}
    assert again is not None and again.checkpoint == seen.checkpoint
    assert again is not seen
    assert len(upstream.reads) == 1


@pytest.mark.asyncio
async def test_descendants_task_path_and_string_versions() -> None:
    upstream = CountingSaver()
    head = await _put(upstream, _config(), "seed")
    saver = await _lease(upstream)
    head_id = head["configurable"]["checkpoint_id"]

    await saver.aput_writes(_config(head_id), [("value", "pending")], "task-1", "~tools")
    child = await _put(saver, _config(head_id), "child")
    latest = await saver.aget_tuple(_config())
    nested = await _put(saver, _config(ns="tools:task-1"), "nested")

    assert upstream.write_paths == ["~tools"]
    assert latest is not None and latest.config == child
    assert nested["configurable"]["checkpoint_ns"] == "tools:task-1"
    with pytest.raises(NotImplementedError):
        BaseCheckpointSaver().get_next_version("00000000000000000000000000000001.0", None)
    version = saver.get_next_version("00000000000000000000000000000001.5", None)
    assert version.startswith("00000000000000000000000000000002.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "namespace", ["evil:task", "tools", "tools:", "tools:task|tools:x/../y", "other|tools:t"]
)
async def test_rejects_arbitrary_nested_namespace(namespace: str) -> None:
    upstream = CountingSaver()
    saver = await _lease(upstream)

    with pytest.raises(CheckpointLineageDeniedError):
        await _put(saver, _config(ns=namespace), "x")


@pytest.mark.asyncio
async def test_denies_foreign_or_global_checkpoint_access() -> None:
    upstream = CountingSaver()
    head = await _put(upstream, _config(), "seed")
    foreign = await _put(upstream, head, "foreign")
    saver = await _lease(upstream, head["configurable"]["checkpoint_id"])
    reads_before = len(upstream.reads)
    foreign_id = foreign["configurable"]["checkpoint_id"]
    other_thread: Any = {"configurable": {"thread_id": "other", "checkpoint_ns": ""}}

    denied = [
        saver.aget_tuple(_config(foreign_id)),
        saver.aget_tuple(other_thread),
        saver.aget_tuple(_config(run="other-run")),
        _put(saver, _config(foreign_id), "fork-from-foreign"),
        _put(saver, _config(), "parentless-root"),
        saver.aput_writes(_config(foreign_id), [("value", "x")], "t", ""),
        saver.adelete_thread(THREAD),
    ]
    for call in denied:
        with pytest.raises(CheckpointLineageDeniedError):
            await call
    with pytest.raises(CheckpointLineageDeniedError):
        saver.get_tuple(_config())
    with pytest.raises(CheckpointLineageDeniedError):
        [item async for item in saver.alist(None)]

    assert len(upstream.reads) == reads_before
    assert upstream.write_paths == []


@pytest.mark.asyncio
async def test_allowlist_clone_shares_lineage() -> None:
    upstream = CountingSaver()
    head = await _put(upstream, _config(), "seed")
    saver = await _lease(upstream)
    clone = saver.with_allowlist([("tests", "Marker")])
    child = await _put(clone, _config(head["configurable"]["checkpoint_id"]), "child")

    latest = await saver.aget_tuple(_config())

    assert latest is not None and latest.config == child


@pytest.mark.asyncio
async def test_factory_binds_global_upstream_with_one_seed_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import checkpointer

    upstream = CountingSaver()
    await _put(upstream, _config(), "seed")
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: upstream)

    saver = await checkpointer.create_lease_checkpointer(
        thread_id=THREAD,
        checkpoint_id=None,
        run_id=RUN,
        child_node_names=("tools",),
        delta_channels=(),
    )

    assert isinstance(saver, LeaseCheckpointSaver)
    assert len(upstream.reads) == 1
    with pytest.raises(CheckpointLineageDeniedError):
        await saver.aget_tuple(_config(run="foreign"))
