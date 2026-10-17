"""Background execution of an operator reconcile run (plan 14.3, S27; H-04).

The route claims and commits the run row, then hands its id to ``launch_reconcile``:
the run continues in this process after the response (tasks are referenced here until
done). If the process dies mid-run, ``reconcile_runs`` closes the row as abandoned once
it has not moved for ``STALE_AFTER``.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from app.authz.client import open_client
from app.authz.config import AuthzSettings
from app.authz.jobs import jobs_enabled
from app.authz.reconcile import Sessions, TupleStore, reconcile_sessions
from app.authz.reconcile_runs import close_stale, execute_run, fail_run
from app.config import settings

logger = logging.getLogger(__name__)
Launcher = Callable[[UUID], None]
StoreOpener = Callable[[AuthzSettings], AbstractAsyncContextManager[TupleStore]]
_tasks: set[asyncio.Task[None]] = set()


async def run_manual(
    run_id: UUID,
    *,
    config: AuthzSettings | None = None,
    opener: StoreOpener = open_client,
    sessions: Sessions | None = None,
) -> None:
    sessions = sessions or reconcile_sessions()
    try:
        async with opener(config or settings) as store:
            await execute_run(sessions, store, run_id, drain=True)
    except Exception as exc:  # noqa: BLE001 - the client could not be opened or closed
        logger.exception("authz_reconcile_failed", extra={"run_id": str(run_id)})
        await fail_run(sessions, run_id, type(exc).__name__)


async def close_stale_at_startup(config: AuthzSettings | None = None) -> None:
    """Lifespan start: release runs whose process died (heartbeat silent); legacy: no-op."""
    if not jobs_enabled(config or settings):
        return
    try:
        await close_stale(reconcile_sessions())
    except Exception:  # noqa: BLE001 - start-up must not fail on this; the tick retries
        logger.exception("authz_reconcile_stale_cleanup_failed")


def launch_reconcile(run_id: UUID) -> None:
    task = asyncio.create_task(run_manual(run_id), name=f"authz-reconcile-{run_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def get_reconcile_launcher() -> Launcher:
    """FastAPI dependency; tests override it to run inline or capture the id."""
    return launch_reconcile
