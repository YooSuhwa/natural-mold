"""Scheduled trigger execution through a prepared lease (parent Task 10, internal I18).

Behind ``settings.prepared_run_lease_enabled`` the trigger invoke block hands its
accepted (committed, ``queued``) run here instead of ``run_owned_execution``:

1. one fresh session: ``lock_trigger_envelope`` (gateway -> Agent tree -> locked
   trigger reload + revalidation), then ``materialize_and_activate`` adds the
   Conversation/Run locks, resolves the trigger-mode inputs (no ``ask_user``,
   ``interrupt_on=None``, trigger memory policy, no MCP approvals), performs the
   single bounded selected-MCP discovery, closes transient resources, writes the
   marker pair plus ``queued -> running``, commits and activates;
2. the activated lease is dispatched through ``execute_prepared_agent_invoke``
   (every provider/tool/child leaf reacquires the I12 permit) under the same
   cancel polling and terminal ``ConversationRun`` transition as the legacy
   owner, so the trigger block keeps projecting ``success|failed|canceled``.

Unsupported agent shapes (``PreparedRunUnsupportedError``, always pre-marker)
fall back to the legacy owned execution, logged. Authority failures of the
gateway/trigger lock itself propagate unchanged (the legacy contract). Every
other pre-activation failure - including ``AppError`` from the locked config
build (missing LLM credential, agent/conversation gone) - is recorded on the
still-unmarked run through ``_settle_unactivated`` (shielded, best effort): a
queued run fails (``PreparedTriggerFailedError``; a trigger paused meanwhile is
``skipped`` like legacy), a cancel requested before start is acknowledged
``canceled``. If even that gateway is denied the original error is re-raised.

Output files: legacy trigger invoke writes model-visible
``/conversations/<thread_id>/...`` files through the shared runtime backend
into the runtime data dir, where they stay on disk after the run. Under the
lease that prefix is routed to ``<prepared_run_root_base()>/<run_id>/output``
and the whole run root is removed after terminalization, so trigger-mode
output files are NOT persisted by this entry (no artifact finalization yet).
"""

from __future__ import annotations

import functools
import logging
import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NoReturn

import anyio
import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner
from app.agent_runtime.identity import AgentIdentityError
from app.agent_runtime.prepared_run_dispatch import handle_for, terminalize_prepared_run
from app.agent_runtime.prepared_run_input_policy import (
    PreparedRunUnsupportedError,
    is_legacy_fallback,
)
from app.agent_runtime.prepared_run_inputs import MaterializerSeams
from app.agent_runtime.prepared_run_transaction import materialize_and_activate
from app.agent_runtime.runtime_config import PreparedRunLease
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.exceptions import AppError
from app.services import agent_execution_runtime as runtime
from app.services import prepared_conversation_executor
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from app.services.conversation_run_state import transition_run
from app.services.conversation_run_worker import get_run_task_registry
from app.services.prepared_run_adapter import PreparedRunRequest, PreparedRunResolver
from app.services.prepared_run_state import fail_prepared_run, mark_prepared_running
from app.services.prepared_trigger_resolver import (
    PreparedTriggerFailedError,
    PreparedTriggerStaleError,
    PreparedTriggerTarget,
    PreparedTriggerTerminalError,
    lock_trigger_envelope,
    trigger_config_resolver,
)

logger = logging.getLogger("app.agent_runtime.trigger_executor")
FAILED = "prepared_trigger_failed"
# The legacy owner's cancel-poll / terminal-transition primitives, reused as-is
# (the ``agent_execution_runtime`` facade is at its size cap).
_OwnedRuntime = runtime._OwnedRuntime  # noqa: SLF001
_poll_cancel = runtime._poll_cancel  # noqa: SLF001
_finish = runtime._finish  # noqa: SLF001
__all__ = ["PreparedTriggerFailedError", "run_prepared_trigger"]


def materializer_seams() -> MaterializerSeams | None:
    """Production uses the default seams; tests patch fakes (scripted model, fake MCP)."""
    return None


def prepared_run_root_base() -> Path:
    """Run roots live outside the runtime data dir (the lease backend forbids sharing)."""
    return prepared_conversation_executor.prepared_run_root()


def _target(request: RuntimeOwnershipRequest) -> RunExecutionTarget:
    conversation = RunConversationTarget(
        request.conversation_id, request.root_agent_id, request.context
    )
    return RunExecutionTarget(request.run_id, conversation)


async def _no_schedule(_lease: PreparedRunLease) -> None:
    """Dispatch happens inline right after activation (trigger invoke is awaited)."""


async def _remove_run_root(run_id: uuid.UUID) -> None:
    path = prepared_run_root_base() / str(run_id)
    with anyio.CancelScope(shield=True):
        try:
            await anyio.to_thread.run_sync(functools.partial(shutil.rmtree, path, True))
        except Exception:  # noqa: BLE001 - best-effort temp cleanup
            logger.warning("Prepared trigger run root cleanup failed: %s", path)


def _codes(exc: BaseException) -> tuple[str, str]:
    """(``ConversationRun.error_code``, trigger-run message) for a pre-activation failure."""
    if isinstance(exc, AgentIdentityError):
        return "agent_identity_error", str(exc)
    code = getattr(exc, "code", None)
    code = code if isinstance(code, str) and code else f"{FAILED}:{type(exc).__name__}"
    return code, code


async def _settle_queued(request: RuntimeOwnershipRequest, code: str) -> str | None:
    """Fail our unmarked queued run, or ack a workerless cancel; ``None`` if neither."""
    try:
        with anyio.CancelScope(shield=True):
            async with runtime.async_session() as db:
                run = await lock_executable_run(db, _target(request))
                outcome = None
                if run is not None and run.runtime_materialized_at is None:
                    if run.status == "queued":
                        await fail_prepared_run(db, run, code)
                        outcome = "failed"
                    elif run.status == "canceling" and run.worker_instance_id is None:
                        await transition_run(
                            db, run, "canceled", allow_workerless_cancellation_ack=True
                        )
                        outcome = "canceled"
                await (db.commit() if outcome else db.rollback())
                return outcome
    except Exception:  # noqa: BLE001 - best effort; the caller re-raises the original
        logger.warning("Prepared trigger run %s could not be settled", request.run_id)
        return None


async def _settle_unactivated(target: PreparedTriggerTarget, exc: Exception) -> NoReturn:
    code, message = _codes(exc)
    outcome = await _settle_queued(target.ownership, code)
    await _remove_run_root(target.ownership.run_id)
    if outcome == "canceled":
        raise PreparedTriggerTerminalError("canceled", None) from exc
    if outcome is None:
        raise exc
    if isinstance(exc, PreparedTriggerStaleError) and exc.reason == "trigger_inactive":
        raise PreparedTriggerTerminalError("skipped", "trigger is not active") from exc
    raise PreparedTriggerFailedError(message) from exc


async def _materialize(
    db: AsyncSession, target: PreparedTriggerTarget, messages: list
) -> tuple[PreparedRunResolver, PreparedRunLease]:
    resolver = PreparedRunResolver(
        PreparedRunRequest(
            context=target.ownership.context,
            resolve_config=trigger_config_resolver(target),
            run_root_base=prepared_run_root_base(),
            messages_history=messages,
            is_trigger_mode=True,
        )
    )
    lease = await materialize_and_activate(
        db,
        _target(target.ownership),
        prepare_inputs=resolver,
        mark_running=mark_prepared_running(get_run_task_registry().worker_instance_id),
        fail_running=fail_prepared_run,
        schedule=_no_schedule,
        session_factory=lambda: runtime.async_session(),
        seams=materializer_seams(),
    )
    return resolver, lease


async def _prepare(target: PreparedTriggerTarget) -> tuple[PreparedRunResolver, PreparedRunLease]:
    locked = False
    async with runtime.async_session() as db:
        try:
            messages = await lock_trigger_envelope(db, target)
            locked = True
            return await _materialize(db, target, messages)
        except PreparedRunUnsupportedError as exc:
            await db.rollback()
            if is_legacy_fallback(exc.reason):
                raise
            failure: Exception = exc
        except (AppError, PermissionError) as exc:
            await db.rollback()
            if not locked:
                raise
            failure = exc
        except Exception as exc:
            await db.rollback()
            failure = exc
    logger.warning("Prepared trigger %s failed before activation", target.trigger_id)
    return await _settle_unactivated(target, failure)


@asynccontextmanager
async def _owned_prepared(request: RuntimeOwnershipRequest) -> AsyncIterator[None]:
    """The legacy owner's supervision for an already running (marked) prepared run.

    Like ``_owned_runtime`` the failure is stored and re-raised only after the
    task group exits, so callers see the original exception, not a group.
    """
    registry = get_run_task_registry()
    owner = _OwnedRuntime(request, registry.worker_instance_id)
    existing = registry.get(request.run_id)
    if existing is not None and not existing.done():
        raise runtime.RuntimeOwnershipConflict(request.run_id, "locally_owned")
    registry.start_current(request.run_id)
    failure: BaseException | None = None
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_poll_cancel, owner)
            try:
                yield
            except (GeneratorExit, anyio.get_cancelled_exc_class()) as error:
                with anyio.CancelScope(shield=True):
                    await _finish(owner, "canceled")
                failure = error
            except BaseException as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                with anyio.CancelScope(shield=True):
                    await _finish(owner, "failed")
                failure = error
            else:
                if not await _finish(owner, "completed"):
                    failure = runtime.RuntimeOwnershipConflict(request.run_id, "owner_changed")
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
    finally:
        registry.discard(request.run_id)


async def run_prepared_trigger(
    target: PreparedTriggerTarget, legacy: Callable[[], Awaitable[str]]
) -> str:
    """Materialize + dispatch the trigger lease; legacy fallback for unsupported shapes."""
    try:
        resolver, lease = await _prepare(target)
    except PreparedRunUnsupportedError as exc:
        logger.info("Trigger %s uses legacy execution: %s", target.trigger_id, exc.code)
        await _remove_run_root(target.ownership.run_id)
        return await runtime.run_owned_execution(target.ownership, legacy)
    cfg = resolver.execution_cfg
    try:
        async with _owned_prepared(target.ownership):
            if cfg is None:
                raise PreparedTriggerFailedError(f"{FAILED}:execution_metadata_missing")
            return await agent_stream_runner.execute_prepared_agent_invoke(
                cfg, handle_for(lease), moldy_source="trigger"
            )
    finally:
        await terminalize_prepared_run(lease)
        await _remove_run_root(target.ownership.run_id)
