"""Run an activated prepared lease: root/subagent dispatch (parent Task 10, I20).

Consume-only. The entry (I16-I19) passes the exact ``PreparedRunHandle``
(binding + token + digest) it received from materialization; dispatch:

1. claims the *active* lease once (``active -> dispatching``) with the exact
   binding, token and digest (``prepared_run_lease_missing`` / ``_mismatch``
   otherwise — never a rebuild; a second dispatch of the same handle is
   ``lease_missing``);
2. runs the frozen compiled root graph inside ``post_marker_execution`` with
   the lease config and lease-bound checkpointer it was compiled with, and the
   lease's run-scoped secret set installed for redaction;
3. on any terminal outcome (success, error, cancellation, early close) closes
   the child-launch ledger for the run (reject new reservations, cancel each
   launched child once) and then terminalizes the registry lease.

It never calls ``_prepare_agent``/component preparation, never compiles,
normalizes or mutates a graph, installs no task handler or permit (the I12
``PreparedTaskDispatchMiddleware``/``LeaseBoundToolMiddleware`` compiled in by
I15 own child admission and every leaf permit), and hands no session or proof
to the graph: children and leaves reacquire their own permits. The runner
bodies skip the DB-writing hook framework (audit/spend) for prepared runs:
those writes are outside the I14 run write capability; I16+ persist usage
through ``PreparedRunWriteCapability``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_registry import CleanupOutcome, get_prepared_run_registry
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease
from app.agent_runtime.runtime_hydration_guard import post_marker_execution

# ``stream_input`` default: feed the lease's frozen converted messages.
USE_LEASE_MESSAGES: Any = object()


@dataclass(frozen=True, slots=True)
class PreparedRunHandle:
    """What an entry keeps between activation and dispatch (no runtime objects)."""

    binding: PreparedRunBinding
    token: str
    digest: str


def handle_for(lease: PreparedRunLease) -> PreparedRunHandle:
    return PreparedRunHandle(lease.binding, lease.token, lease.digest)


def claim_prepared_lease(handle: PreparedRunHandle) -> PreparedRunLease:
    """Single-use claim of the exact active lease, or ``PreparedRunLeaseError``."""

    return get_prepared_run_registry().claim_dispatch(
        handle.binding, token=handle.token, digest=handle.digest
    )


async def terminalize_prepared_run(lease: PreparedRunLease) -> CleanupOutcome:
    """Stop child launches (cancelling launched children once), then close the lease."""

    run_id = lease.binding.run_id
    get_child_launch_ledger().terminalize(run_id)
    return await get_prepared_run_registry().terminalize(run_id)


def lease_graph_input(lease: PreparedRunLease, stream_input: Any = USE_LEASE_MESSAGES) -> Any:
    """The root input: frozen messages (``None`` when empty = time travel) or an override.

    An override is a caller value built before the marker (``Command(resume=...)``
    for HiTL resume, an ``Overwrite`` state dict for fork-edit).
    """

    if stream_input is not USE_LEASE_MESSAGES:
        return stream_input
    messages = list(lease.messages)
    return {"messages": messages} if messages else None


def lease_run_config(lease: PreparedRunLease) -> dict[str, Any]:
    """A fresh copy of the frozen lease config (callers may add callbacks)."""

    config: dict[str, Any] = dict(lease.config)
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        config["configurable"] = dict(configurable)
    return config


@asynccontextmanager
async def prepared_dispatch(handle: PreparedRunHandle) -> AsyncIterator[PreparedRunLease]:
    """Hold the claimed lease for one dispatch; always terminalize on exit.

    A failed claim terminalizes nothing it does not own: a mismatched handle
    leaves the real lease untouched, a missing lease is already gone.
    """

    lease = claim_prepared_lease(handle)
    secret_token = set_run_secrets(set(lease.secret_values))
    try:
        with post_marker_execution(str(lease.binding.run_id)):
            yield lease
    finally:
        try:
            reset_run_secrets(secret_token)
        finally:
            await terminalize_prepared_run(lease)


async def invoke_prepared(
    handle: PreparedRunHandle, *, stream_input: Any = USE_LEASE_MESSAGES
) -> Any:
    """Invoke the frozen root graph once and return its final state."""

    async with prepared_dispatch(handle) as lease:
        return await lease.runnable.ainvoke(
            lease_graph_input(lease, stream_input), lease_run_config(lease)
        )
