"""Bind checkpoint access for one ordinary chat to its current worker generation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.dialects.postgresql.psycopg import dialect

from app.chat_recovery.fence import (
    RunFence,
    RunOwnershipLost,
    current_run_fence,
    lock_run_fence,
    open_run_session,
    run_fence_statement,
)
from app.models.conversation_run import ConversationRun


def fenced_chat_checkpointer(saver: BaseCheckpointSaver[Any]) -> BaseCheckpointSaver[Any]:
    fence = current_run_fence()
    return RecoveryCheckpointSaver(saver, fence) if fence is not None else saver


@asynccontextmanager
async def _postgres_connection(saver: AsyncPostgresSaver) -> AsyncIterator[Any]:
    if isinstance(saver.conn, AsyncConnectionPool):
        async with saver.conn.connection() as connection:
            yield connection
    else:
        async with saver.lock:
            yield saver.conn


@asynccontextmanager
async def _locked_saver(
    saver: BaseCheckpointSaver[Any], fence: RunFence
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    if isinstance(saver, AsyncPostgresSaver):
        # Put the row lock and checkpoint write in ONE server-side transaction.
        # If the worker process disappears, PostgreSQL releases the lock only
        # after committing or rolling back the very same checkpoint mutation.
        statement = run_fence_statement(fence).with_only_columns(ConversationRun.id)
        compiled = statement.compile(dialect=dialect(), compile_kwargs={"render_postcompile": True})
        async with _postgres_connection(saver) as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.scope', '', true), "
                "set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true)",
                (str(fence.tenant.tenant_id), str(fence.tenant.user_id)),
            )
            cursor = await connection.execute(str(compiled), compiled.params)
            if await cursor.fetchone() is None:
                raise RunOwnershipLost()
            yield AsyncPostgresSaver(connection, serde=saver.serde)
        return
    # Development/test savers have no checkpoint database connection.
    async with open_run_session(fence) as db:
        await lock_run_fence(db, fence)
        yield saver


class RecoveryCheckpointSaver(BaseCheckpointSaver[Any]):
    """Allow graph reads and writes for exactly one conversation and current owner.

    Administrative and synchronous methods inherit the base's non-implementations.
    The graph is always driven asynchronously for recoverable chat runs.
    """

    def __init__(self, upstream: BaseCheckpointSaver[Any], fence: RunFence) -> None:
        super().__init__(serde=upstream.serde)
        self._upstream = upstream
        self._fence = fence

    def with_allowlist(
        self, extra_allowlist: Collection[tuple[str, ...]]
    ) -> RecoveryCheckpointSaver:
        upstream = self._upstream.with_allowlist(extra_allowlist)
        if upstream is self._upstream:
            return self
        return RecoveryCheckpointSaver(upstream, self._fence)

    @property
    def config_specs(self) -> list:
        return self._upstream.config_specs

    def get_next_version(self, current: Any, channel: None) -> Any:
        return self._upstream.get_next_version(current, channel)

    def _check(self, config: RunnableConfig | None) -> None:
        identity = (config or {}).get("configurable") or {}
        if (
            identity.get("thread_id") != str(self._fence.conversation_id)
            or identity.get("moldy_run_id", str(self._fence.run_id)) != str(self._fence.run_id)
            or identity.get("moldy_tenant_id", str(self._fence.tenant.tenant_id))
            != str(self._fence.tenant.tenant_id)
        ):
            raise RunOwnershipLost()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self._check(config)
        async with _locked_saver(self._upstream, self._fence) as saver:
            return await saver.aget_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 - upstream signature
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        self._check(config)
        if before is not None:
            self._check(before)
        # Do not hold a row lock or database connection while graph code runs
        # between yielded items. A new owner fences further reads immediately.
        remaining = limit
        while remaining is None or remaining > 0:
            async with _locked_saver(self._upstream, self._fence) as saver:
                page = [
                    row async for row in saver.alist(config, filter=filter, before=before, limit=1)
                ]
            if not page:
                return
            yield page[0]
            before = page[0].config
            if remaining is not None:
                remaining -= 1

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        self._check(config)
        async with _locked_saver(self._upstream, self._fence) as saver:
            return await saver.aget_delta_channel_history(config=config, channels=channels)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        self._check(config)
        async with _locked_saver(self._upstream, self._fence) as saver:
            # LangGraph can pass only a thread/namespace/checkpoint on a later
            # write. Persist the trusted binding even when that config is thin.
            owned = cast(
                CheckpointMetadata,
                {
                    **metadata,
                    "moldy_run_id": str(self._fence.run_id),
                    "moldy_tenant_id": str(self._fence.tenant.tenant_id),
                },
            )
            return await saver.aput(config, checkpoint, owned, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._check(config)
        async with _locked_saver(self._upstream, self._fence) as saver:
            await saver.aput_writes(config, writes, task_id, task_path)
