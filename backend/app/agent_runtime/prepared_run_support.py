"""Small building blocks for prepared-run materialization (parent Task 10, I15).

* :class:`OnceCloser` — the single cleanup owner of every lease-owned resource;
  however many failure paths call it, each closer runs exactly once.
* :class:`LateBoundAuthority` — lease models are constructed before the lease
  digest exists (the census needs the real model to probe the installed
  compiler), so their ``LeaseSendGuard`` opens permits through this opener,
  which binds to the authority lease once it is built and fails closed before.
* :func:`bounded_cleanup` — failure-path cleanup that can neither hang the
  caller past a timeout nor replace the original failure.
* census helpers that close each participant's effective surface.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from app.agent_runtime.lease_dispatch_permit import (
    DispatchDeniedError,
    DispatchPermit,
    PermitKind,
    SessionFactory,
    open_dispatch_permit,
)
from app.agent_runtime.prepared_run_inputs import Closer
from app.agent_runtime.prepared_run_registry import LEASE_MISSING
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease
from app.agent_runtime.runtime_effective_capabilities import (
    EffectiveCapabilityCensus,
    ProviderOrigin,
    build_effective_census,
)
from app.agent_runtime.tool_access_policy import ToolOrigin

CLEANUP_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)
_abandoned: set[asyncio.Future[Any]] = set()


async def bounded_cleanup(
    step: Callable[[], Awaitable[Any]],
    *,
    label: str,
    limit_seconds: float = CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """Run one cleanup step shielded and bounded; log (never raise) its failure."""

    task = asyncio.ensure_future(step())
    try:
        done, _pending = await asyncio.shield(asyncio.wait({task}, timeout=limit_seconds))
    except BaseException:  # noqa: BLE001 - the caller's original failure wins
        _abandoned.add(task)
        task.add_done_callback(_abandoned.discard)
        logger.warning("prepared run cleanup interrupted step=%s", label)
        return
    if not done:
        task.cancel()
        _abandoned.add(task)
        task.add_done_callback(_abandoned.discard)
        logger.warning("prepared run cleanup timed out step=%s", label)
        return
    if task.cancelled() or task.exception() is not None:
        error = "CancelledError" if task.cancelled() else type(task.exception()).__name__
        logger.warning("prepared run cleanup failed step=%s error=%s", label, error)


class OnceCloser:
    """Run registered closers once, newest first; the first error is re-raised."""

    def __init__(self) -> None:
        self._closers: list[Closer] = []
        self._task: asyncio.Future[None] | None = None
        self.calls = 0

    def add(self, closer: Closer) -> None:
        if self._task is not None:
            raise RuntimeError("lease resources are already closed")
        self._closers.append(closer)

    async def __call__(self) -> None:
        self.calls += 1
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())
        await asyncio.shield(self._task)

    async def _run(self) -> None:
        first: BaseException | None = None
        for closer in reversed(self._closers):
            try:
                await closer()
            except Exception as error:  # noqa: BLE001 - every closer still runs
                first = first or error
        if first is not None:
            raise first


class LateBoundAuthority:
    """Permit opener for guards built before the authority lease exists."""

    def __init__(self) -> None:
        self.lease: PreparedRunLease | None = None

    def open(
        self,
        lease: PreparedRunLease,
        *,
        kind: PermitKind,
        descriptor_id: str,
        allowed_descriptors: frozenset[str],
        session_factory: SessionFactory,
    ) -> AbstractAsyncContextManager[DispatchPermit]:
        del lease, allowed_descriptors  # the bound authority lease is the only source
        return self._open(kind, descriptor_id, session_factory)

    @asynccontextmanager
    async def _open(
        self, kind: PermitKind, descriptor_id: str, session_factory: SessionFactory
    ) -> AsyncIterator[DispatchPermit]:
        bound = self.lease
        if bound is None:
            raise DispatchDeniedError(LEASE_MISSING)
        async with open_dispatch_permit(
            bound,
            kind=kind,
            descriptor_id=descriptor_id,
            allowed_descriptors=bound.descriptor_ids,
            session_factory=session_factory,
        ) as permit:
            yield permit


def unbound_lease(binding: PreparedRunBinding) -> PreparedRunLease:
    """Placeholder handed to ``LeaseSendGuard``; the late-bound opener ignores it."""

    return PreparedRunLease(
        binding=binding,
        token="",
        digest="",
        runnable=None,
        child_runnables={},
        messages=(),
        config={},
        checkpointer=None,
        checkpoint_seed=None,
        snapshots={},
        tool_access={},
        secret_values=frozenset(),
    )


def provider_descriptor_id(participant: str, origin: ProviderOrigin) -> str:
    return f"provider:{participant}:{origin.role}:{origin.provider}:{origin.model_name}"


def participant_census(
    participant: str,
    *,
    surface: Sequence[str],
    origins: Sequence[ToolOrigin],
    middleware: Sequence[Any],
    backend: Any,
    providers: Sequence[ProviderOrigin],
    preloaded: Mapping[str, Iterable[str]],
) -> EffectiveCapabilityCensus:
    names = set(surface)
    return build_effective_census(
        participant,
        effective_tool_names=surface,
        selected_tools=[item for item in origins if item.name in names],
        middleware=middleware,
        backends={participant: backend},
        providers=providers,
        preloaded_resources={key: value for key, value in preloaded.items() if key in names},
    )
