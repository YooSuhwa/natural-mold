"""Run task registry extracted from ``conversation_run_worker`` (Task 10 I4).

Collaborators that tests patch on the legacy ``conversation_run_worker`` facade
are resolved through that facade at call time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("app.services.conversation_run_worker")

CancelReason = Literal["stop", "steer", "shutdown"]
_CANCEL_POLL_SECONDS = 0.25


def worker_attr(name: str) -> Any:
    """Return ``conversation_run_worker.<name>`` as currently bound."""

    from app.services import conversation_run_worker

    return getattr(conversation_run_worker, name)


def _session_factory():
    return worker_attr("_session_factory")()


def _tenant_session(scope: Any) -> Any:
    return worker_attr("_tenant_session")(scope)


class RunTaskRegistrationError(RuntimeError):
    pass


class RunTaskAlreadyRegisteredError(RunTaskRegistrationError):
    pass


class RunTaskRegistry:
    def __init__(self, *, worker_instance_id: str | None = None) -> None:
        self.worker_instance_id = worker_instance_id or f"worker-{uuid.uuid4().hex[:16]}"
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._cancel_reasons: dict[uuid.UUID, CancelReason] = {}
        self._finishing: set[uuid.UUID] = set()

    def start(self, run_id: uuid.UUID, task: asyncio.Task[None]) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            task.cancel()
            raise RunTaskAlreadyRegisteredError(f"run task already exists: {run_id}")
        self._tasks[run_id] = task

        def _discard(done_task: asyncio.Task[None]) -> None:
            self.discard(run_id)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.error(
                        "conversation run task crashed run_id=%s",
                        run_id,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

        task.add_done_callback(_discard)

    def start_current(self, run_id: uuid.UUID) -> asyncio.Task[None]:
        """Register and return the task currently executing this coroutine."""
        task = asyncio.current_task()
        if task is None:
            raise RunTaskRegistrationError("no current task is available for registration")
        self.start(run_id, task)
        return task

    def get(self, run_id: uuid.UUID) -> asyncio.Task[None] | None:
        return self._tasks.get(run_id)

    def active_run_ids(self) -> set[uuid.UUID]:
        return {run_id for run_id, task in self._tasks.items() if not task.done()}

    def cancel(self, run_id: uuid.UUID) -> bool:
        return self.request_cancel(run_id, reason="stop")

    def mark_finishing(self, run_id: uuid.UUID) -> None:
        """The run body has ended; its finalization reconciles a later stop under the row lock.

        A local cancel from then on would only tear the terminal transition, the broker
        close and the queue dispatch, so :meth:`request_cancel` ignores finishing runs.
        """
        self._finishing.add(run_id)

    def request_cancel(self, run_id: uuid.UUID, *, reason: CancelReason) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done() or run_id in self._finishing:
            return False
        self._cancel_reasons[run_id] = reason
        if task.cancelling() == 0:
            task.cancel()
        return True

    def cancel_reason(self, run_id: uuid.UUID) -> CancelReason | None:
        return self._cancel_reasons.get(run_id)

    def discard(self, run_id: uuid.UUID) -> None:
        self._tasks.pop(run_id, None)
        self._cancel_reasons.pop(run_id, None)
        self._finishing.discard(run_id)

    async def shutdown(self, timeout_seconds: float = 10.0) -> None:
        items = [(run_id, task) for run_id, task in self._tasks.items() if not task.done()]
        try:
            await worker_attr("_persist_shutdown_intent")([run_id for run_id, _task in items])
        except SQLAlchemyError:
            logger.exception("failed to persist conversation run shutdown intent")
        for run_id, _task in items:
            self.request_cancel(run_id, reason="shutdown")
        tasks = [task for _, task in items]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "timed out waiting for %d conversation run task(s) during shutdown",
                    len(tasks),
                )
        self._tasks.clear()
        self._cancel_reasons.clear()
        self._finishing.clear()
