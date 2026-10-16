"""Atomic materialize -> mark -> commit -> activate -> schedule (parent Task 10, I15).

Inside one canonical gateway transaction: lock the queued run (Tenant ->
Organization -> owner memberships -> Agent lifecycle -> Conversation -> Run),
*then* resolve the preparation inputs through the caller's
``prepare_inputs(db, run)`` (so every input is read under those locks), build
and stage the complete lease, close the transient preparation resources, write
the non-secret marker pair (``runtime_materialized_at`` + digest) and
``queued -> running`` together, flush and commit. Only then is the lease
activated and handed to the scheduling hook.

Failure matrix (cleanup is shielded, time-bounded and never replaces the
original error):

* before commit (resolve, prepare, transient close, flush, commit, cancel):
  transient closers run exactly once each, the transaction rolls back and the
  reservation is terminalized (lease closed exactly once). If commit was
  attempted, a fresh gateway session fails the row with
  ``prepared_run_commit_in_doubt`` when it finds our exact marker committed.
* after commit (activation or scheduling): the lease is terminalized, then a
  fresh gateway session durably fails the run (``prepared_run_activation_failed``
  / ``prepared_run_schedule_failed``) through the injected ``fail_running``.
  A marker without a live lease fails every later acquire with
  ``prepared_run_lease_missing``; it is never rebuilt.
* a failure after ``reserve`` but before commit leaves the row ``queued`` with
  a retired run ID: the caller (internal I16) must durably fail that row.

Run-state writes are injected by the service layer (``agent_runtime`` must not
import ``app.services``); the scheduling hook is the caller's (internal I20
consumes the activated lease without this module importing it).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import SessionFactory
from app.agent_runtime.prepared_run_inputs import MaterializerSeams, PreparationInputs
from app.agent_runtime.prepared_run_materializer import Trace, materialize_prepared_lease
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_run_support import OnceCloser, bounded_cleanup
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease
from app.authz.conversation_run_scope import RunExecutionTarget, lock_executable_run
from app.models.conversation_run import ConversationRun

RUN_NOT_MATERIALIZABLE = "prepared_run_not_materializable"
ACTIVATION_FAILED = "prepared_run_activation_failed"
SCHEDULE_FAILED = "prepared_run_schedule_failed"
COMMIT_IN_DOUBT = "prepared_run_commit_in_doubt"
PrepareInputs = Callable[[AsyncSession, ConversationRun], Awaitable[PreparationInputs]]
MarkRunning = Callable[[AsyncSession, ConversationRun], Awaitable[Any]]
FailRunning = Callable[[AsyncSession, ConversationRun, str], Awaitable[Any]]
Schedule = Callable[[PreparedRunLease], Awaitable[None]]


class PreparedRunTransactionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _noop(_event: str) -> None:
    return None


def _binding(target: RunExecutionTarget, thread_id: str) -> PreparedRunBinding:
    conversation = target.conversation
    context = conversation.context
    return PreparedRunBinding(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        agent_id=conversation.agent_id,
        conversation_id=conversation.conversation_id,
        run_id=target.run_id,
        thread_id=thread_id,
    )


async def _fail_durably(
    session_factory: SessionFactory,
    target: RunExecutionTarget,
    fail_running: FailRunning,
    code: str,
    digest: str,
) -> None:
    """Re-enter the gateway in a fresh session and fail our exact marked run."""

    async with session_factory() as db:
        run = await lock_executable_run(db, target)
        if run is None or run.status != "running" or run.runtime_materialization_sha256 != digest:
            await db.rollback()
            return
        await fail_running(db, run, code)
        await db.commit()


async def materialize_and_activate(
    db: AsyncSession,
    target: RunExecutionTarget,
    *,
    prepare_inputs: PrepareInputs,
    mark_running: MarkRunning,
    fail_running: FailRunning,
    schedule: Schedule,
    session_factory: SessionFactory,
    seams: MaterializerSeams | None = None,
    trace: Trace = _noop,
) -> PreparedRunLease:
    """Materialize, mark and commit atomically, then activate and schedule."""

    registry = get_prepared_run_registry()
    transient = OnceCloser()
    lease: PreparedRunLease | None = None
    commit_attempted = False

    def fail(code: str, digest: str) -> Callable[[], Awaitable[None]]:
        return lambda: _fail_durably(session_factory, target, fail_running, code, digest)

    def terminalize() -> Awaitable[Any]:
        return registry.terminalize(target.run_id)

    try:
        run = await lock_executable_run(db, target)
        if run is None or run.status != "queued" or run.runtime_materialized_at is not None:
            raise PreparedRunTransactionError(RUN_NOT_MATERIALIZABLE)
        trace("locks")
        inputs = await prepare_inputs(db, run)
        for closer in inputs.transient_closers:
            transient.add(closer)
        trace("prepare_inputs")
        materialized = await materialize_prepared_lease(
            _binding(target, inputs.thread_id),
            inputs,
            session_factory=session_factory,
            seams=seams,
            trace=trace,
        )
        lease = materialized.lease
        await transient()
        trace("close_transient")
        run.runtime_materialized_at = datetime.now(UTC)
        run.runtime_materialization_sha256 = lease.digest
        await mark_running(db, run)
        trace("marker_running")
        await db.flush()
        commit_attempted = True
        await db.commit()
        trace("commit")
    except BaseException:
        try:
            await bounded_cleanup(transient, label="transient")
            await bounded_cleanup(db.rollback, label="rollback")
        finally:
            if lease is not None:
                await bounded_cleanup(terminalize, label="lease")
                if commit_attempted:
                    await bounded_cleanup(fail(COMMIT_IN_DOUBT, lease.digest), label="in_doubt")
        raise
    code = ACTIVATION_FAILED
    try:
        registry.activate(target.run_id, token=lease.token, digest=lease.digest)
        trace("activate")
        code = SCHEDULE_FAILED
        await schedule(lease)
        trace("schedule")
    except BaseException:
        try:
            await bounded_cleanup(terminalize, label="lease")
        finally:
            await bounded_cleanup(fail(code, lease.digest), label="durable_fail")
        raise
    return lease
