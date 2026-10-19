"""Checkpoint saver bound to one prepared run lease's lineage (parent Task 10).

Execution may read only the frozen seed (captured once under locks by
``checkpoint_seed``) and checkpoints this lease itself wrote, under the exact
thread, an authorized namespace and the lease's ``moldy_run_id``. Writes are
delegated to the upstream saver; version generation delegates to the upstream
string semantics. Global/latest lookups beyond the lineage, foreign parents,
sync methods and copy/delete/prune are denied with ``checkpoint_lineage_denied``.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from typing import Any, NoReturn

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
    PendingWrite,
)

from app.agent_runtime.checkpoint_seed import CheckpointSeed
from app.agent_runtime.lease_checkpoint_namespace import (
    CheckpointLineageDeniedError,
    LeaseNamespaceAuthority,
)


class _Lineage:
    """Shared mutable lineage state (``with_allowlist`` clones share it)."""

    def __init__(self) -> None:
        self.owned: dict[str, list[str]] = {}
        self.seed_writes: dict[str, list[tuple[str, Sequence[tuple[str, Any]]]]] = {}


class LeaseCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(
        self,
        upstream: BaseCheckpointSaver[Any],
        *,
        seed: CheckpointSeed,
        run_id: str,
        namespaces: LeaseNamespaceAuthority,
    ) -> None:
        super().__init__(serde=upstream.serde)
        self._upstream = upstream
        self._seed = seed
        self._run_id = run_id
        self._namespaces = namespaces
        self._lineage = _Lineage()
        for namespace in sorted(seed.entries, key=lambda item: item.count("|")):
            namespaces.authorize(namespace)

    def with_allowlist(self, extra_allowlist: Collection[tuple[str, ...]]) -> LeaseCheckpointSaver:
        upstream = self._upstream.with_allowlist(extra_allowlist)
        if upstream is self._upstream:
            return self
        clone = copy.copy(self)
        clone._upstream = upstream  # noqa: SLF001 - same-class clone
        clone.serde = upstream.serde
        return clone

    @property
    def config_specs(self) -> list:
        return self._upstream.config_specs

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self._upstream.get_next_version(current, channel)

    # -- lineage checks -----------------------------------------------------

    def _scope(self, config: RunnableConfig | None) -> tuple[str, str | None]:
        configurable = (config or {}).get("configurable") or {}
        if configurable.get("thread_id") != self._seed.thread_id:
            raise CheckpointLineageDeniedError("thread")
        if configurable.get("moldy_run_id") not in (None, self._run_id):
            raise CheckpointLineageDeniedError("run")
        namespace = configurable.get("checkpoint_ns", "")
        if not isinstance(namespace, str):
            raise CheckpointLineageDeniedError("namespace")
        self._namespaces.authorize(namespace)
        checkpoint_id = configurable.get("checkpoint_id")
        return namespace, checkpoint_id if isinstance(checkpoint_id, str) else None

    def _is_seed(self, namespace: str, checkpoint_id: str | None) -> bool:
        entry = self._seed.entries.get(namespace)
        return entry is not None and checkpoint_id == entry.checkpoint_id

    def _read_seed(self, namespace: str) -> CheckpointTuple:
        return self._seed.entries[namespace].read(self._lineage.seed_writes.get(namespace, []))

    def _is_owned(self, namespace: str, checkpoint_id: str | None) -> bool:
        return checkpoint_id is not None and checkpoint_id in self._lineage.owned.get(namespace, ())

    def _exact(self, namespace: str, checkpoint_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": self._seed.thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    # -- reads --------------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        namespace, checkpoint_id = self._scope(config)
        if checkpoint_id is None:
            owned = self._lineage.owned.get(namespace)
            if owned:
                checkpoint_id = owned[-1]
            elif namespace in self._seed.entries:
                return self._read_seed(namespace)
            else:
                return None
        if self._is_seed(namespace, checkpoint_id):
            return self._read_seed(namespace)
        if not self._is_owned(namespace, checkpoint_id):
            raise CheckpointLineageDeniedError("checkpoint")
        return await self._upstream.aget_tuple(self._exact(namespace, checkpoint_id))

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 - BaseCheckpointSaver signature
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None or filter:
            raise CheckpointLineageDeniedError("list")
        namespace, _checkpoint_id = self._scope(config)
        ids = list(reversed(self._lineage.owned.get(namespace, [])))
        if namespace in self._seed.entries:
            ids.append(self._seed.entries[namespace].checkpoint_id)
        if before is not None:
            # Subgraph replay asks for the newest checkpoint before the fork
            # point; only this lease's own lineage can answer (others: empty).
            bound = (before.get("configurable") or {}).get("checkpoint_id")
            if not isinstance(bound, str):
                raise CheckpointLineageDeniedError("list")
            ids = [item for item in ids if item < bound]
        for checkpoint_id in ids[:limit] if limit is not None else ids:
            saved = await self.aget_tuple(self._exact(namespace, checkpoint_id))
            if saved is not None:
                yield saved

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        if not channels:
            return {}
        namespace, _requested = self._scope(config)
        collected: dict[str, list[PendingWrite]] = {name: [] for name in channels}
        seeds: dict[str, Any] = {}
        remaining = set(channels)
        target = await self.aget_tuple(config)
        cursor = target.parent_config if target is not None else None
        target_id = (
            None if target is None else target.config.get("configurable", {}).get("checkpoint_id")
        )
        reached_seed = target is not None and self._is_seed(namespace, target_id)
        seed_ns = namespace
        while cursor is not None and remaining and not reached_seed:
            parent_ns, parent_id = self._scope(cursor)
            saved = await self.aget_tuple(cursor)
            if saved is None:
                break
            for write in reversed(saved.pending_writes or []):
                if write[1] in remaining:
                    collected[write[1]].append(write)
            for name in list(remaining):
                if name in saved.checkpoint["channel_values"]:
                    seeds[name] = saved.checkpoint["channel_values"][name]
                    remaining.discard(name)
            reached_seed = self._is_seed(parent_ns, parent_id)
            seed_ns = parent_ns
            cursor = saved.parent_config
        result: dict[str, DeltaChannelHistory] = {}
        for name in channels:
            writes = list(reversed(collected[name]))
            entry: DeltaChannelHistory = {"writes": writes}
            if name in remaining and reached_seed:
                frozen = self._seed.entries[seed_ns].delta_history.get(name)
                if frozen is None:
                    raise CheckpointLineageDeniedError("delta_history")
                entry = {"writes": [*frozen["writes"], *writes]}
                if "seed" in frozen:
                    entry["seed"] = frozen["seed"]
            elif name in seeds:
                entry["seed"] = seeds[name]
            result[name] = entry
        return result

    # -- writes -------------------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        namespace, parent_id = self._scope(config)
        if parent_id is not None and not (
            self._is_seed(namespace, parent_id) or self._is_owned(namespace, parent_id)
        ):
            raise CheckpointLineageDeniedError("parent")
        if parent_id is None and (
            self._lineage.owned.get(namespace) or namespace in self._seed.entries
        ):
            raise CheckpointLineageDeniedError("parent")
        stored = await self._upstream.aput(config, checkpoint, metadata, new_versions)
        stored_id = stored.get("configurable", {}).get("checkpoint_id")
        if not isinstance(stored_id, str):
            raise CheckpointLineageDeniedError("stored")
        self._lineage.owned.setdefault(namespace, []).append(stored_id)
        return stored

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        namespace, checkpoint_id = self._scope(config)
        if not (
            self._is_seed(namespace, checkpoint_id) or self._is_owned(namespace, checkpoint_id)
        ):
            raise CheckpointLineageDeniedError("writes")
        await self._upstream.aput_writes(config, writes, task_id, task_path)
        if self._is_seed(namespace, checkpoint_id):
            self._lineage.seed_writes.setdefault(namespace, []).append((task_id, list(writes)))

    # -- denied surface -----------------------------------------------------

    def _deny(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise CheckpointLineageDeniedError("method")

    get_tuple = list = put = put_writes = delete_thread = delete_for_runs = _deny  # type: ignore[assignment]
    copy_thread = prune = get_delta_channel_history = _deny  # type: ignore[assignment]

    async def adelete_thread(self, thread_id: str) -> None:
        self._deny()

    async def adelete_for_runs(self, run_ids: Any) -> None:
        self._deny()

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        self._deny()

    async def aprune(self, thread_ids: Any, *, strategy: str = "keep_latest") -> None:
        self._deny()
