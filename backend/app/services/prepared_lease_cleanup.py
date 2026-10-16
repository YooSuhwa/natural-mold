"""Shielded cleanup shared by the prepared chat/queue, API and trigger entries (I24 H4/M1/M2).

* :func:`abandon_activated_lease` closes a lease that was committed and
  activated but never claimed by its dispatcher (cancellation at the session
  close, missing execution metadata, ...): the registry lease and the child
  ledger are terminalized and the marked run is durably failed with
  ``prepared_run_activation_failed`` in a fresh gateway session.
* :func:`fail_owned_run` fails a running prepared run with the shared error
  code table (:mod:`app.agent_runtime.run_error_codes`) before the legacy
  ``_finish`` terminal (which then sees a terminal row and does nothing).
* :func:`remove_run_root` removes a run's scratch/output root after
  terminalization (best effort, never raises).

Every helper runs shielded so a caller's cancellation cannot interrupt it.
"""

from __future__ import annotations

import functools
import logging
import shutil
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread

from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_registry import PreparedRunLeaseError, get_prepared_run_registry
from app.agent_runtime.prepared_run_transaction import ACTIVATION_FAILED, _fail_durably
from app.agent_runtime.run_error_codes import denial_error_code
from app.authz.conversation_run_scope import RunExecutionTarget
from app.services.prepared_run_state import fail_prepared_run

logger = logging.getLogger(__name__)


def _owned() -> Any:
    """Call-time facade (tests patch ``agent_execution_runtime.async_session``)."""

    from app.services import agent_execution_runtime

    return agent_execution_runtime


def _sessions() -> Any:
    return _owned().async_session()


async def abandon_activated_lease(
    target: RunExecutionTarget, digest: str, code: str = ACTIVATION_FAILED
) -> None:
    """Terminalize an unclaimed active lease and durably fail its marked run."""

    run_id = target.run_id
    with anyio.CancelScope(shield=True):
        try:
            get_child_launch_ledger().terminalize(run_id)
            await get_prepared_run_registry().terminalize(run_id)
        finally:
            try:
                await _fail_durably(_sessions, target, fail_prepared_run, code, digest)
            except Exception:  # noqa: BLE001 - best effort; the original error propagates
                logger.warning("prepared run %s could not be failed after activation", run_id)


def failure_code(error: BaseException) -> str | None:
    if isinstance(error, PreparedRunLeaseError):
        return error.code
    return denial_error_code(error)


async def fail_owned_run(runtime: Any, error: BaseException, digest: str) -> None:
    """Fail a running prepared run with its mapped code, then the legacy terminal."""

    owned = _owned()
    runtime.flags.finishing = True
    code = failure_code(error)
    if code is not None:
        target = owned._execution_target(runtime.request)  # noqa: SLF001
        await _fail_durably(_sessions, target, fail_prepared_run, code, digest)
    await owned._finish(runtime, "failed")  # noqa: SLF001


async def remove_run_root(path: Path) -> None:
    with anyio.CancelScope(shield=True):
        try:
            await anyio.to_thread.run_sync(functools.partial(shutil.rmtree, path, True))
        except Exception:  # noqa: BLE001 - best-effort temp cleanup
            logger.warning("prepared run root cleanup failed")


__all__ = ["abandon_activated_lease", "fail_owned_run", "failure_code", "remove_run_root"]
