"""The worker process: claim due jobs, run their handlers, keep leases alive.

Run as its own process (``python -m app.jobs.worker``; the Compose ``worker`` service).
Each loop claims at most ``concurrency`` jobs in a platform session, then runs each
handler with a session bound to the job's company and requester. A background task
extends the lease every third of it; a handler that raises ``JobFailed(retry=False)`` or
any error after its last attempt ends the job as failed. Leases of a worker that dies
run out and another worker takes the job again (``service.claim``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid
from collections.abc import Callable
from typing import Any

from app.database import SessionTenant, platform_session, tenant_sessions
from app.jobs import builtin, registry, service
from app.jobs.context import JobContext
from app.jobs.registry import JobCanceled, JobFailed
from app.models.job import Job

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        concurrency: int = 2,
        poll_seconds: float = 2.0,
        maker: Callable[[], Any] | None = None,
    ) -> None:
        builtin.register_builtin()
        from app.services.memory_index import register_jobs as register_memory_jobs

        register_memory_jobs()
        from app.services.memory_consolidation import register_jobs as register_consolidation

        register_consolidation()
        from app.services.memory_trigger_memo import register_jobs as register_trigger_memo

        register_trigger_memo()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        # Tests pass their own session maker; production uses the database module's.
        self._maker = maker
        self._running: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._started = service.now()

    def _platform(self) -> Any:
        if self._maker is None:
            return platform_session("jobs.worker")
        return platform_session("jobs.worker", maker=self._maker)

    def _company(self, claim: service.Claim) -> Callable[[], Any]:
        tenant = SessionTenant(claim.tenant_id, claim.actor_id)
        if self._maker is None:
            return tenant_sessions(tenant)
        return tenant_sessions(tenant, maker=self._maker)

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        """Claim and start as many due jobs as there are free slots; returns how many."""
        started = 0
        types = registry.names()
        lease = {name: registry.get(name).lease_seconds for name in types}  # type: ignore[union-attr]
        while len(self._running) < self.concurrency:
            async with self._platform() as db:
                claim = await service.claim(db, worker_id=self.worker_id, types=types, lease=lease)
            if claim is None:
                break
            task = asyncio.create_task(self._run(claim))
            self._running.add(task)
            task.add_done_callback(self._running.discard)
            started += 1
        return started

    async def drain(self) -> None:
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)

    async def run_forever(self) -> None:
        logger.info("job worker %s started for %s", self.worker_id, registry.names())
        try:
            while not self._stop.is_set():
                await self._beat_worker()
                await self.run_once()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), self.poll_seconds)
        finally:
            await self.drain()
            async with self._platform() as db:
                await service.forget_worker(db, self.worker_id)
            logger.info("job worker %s stopped", self.worker_id)

    async def _beat_worker(self) -> None:
        try:
            async with self._platform() as db:
                await service.record_worker(
                    db,
                    self.worker_id,
                    hostname=socket.gethostname(),
                    types=registry.names(),
                    started=self._started,
                )
        except Exception:
            logger.exception("job worker heartbeat failed")

    async def _run(self, claim: service.Claim) -> None:
        job_type = registry.get(claim.type)
        assert job_type is not None  # noqa: S101 - claimed only for registered types
        company = self._company(claim)
        async with company() as db:
            job = await db.get(Job, claim.job_id)
            payload = dict(job.payload) if job is not None else {}

        async def beat(progress: int | None = None, note: str | None = None) -> None:
            async with company() as db:
                answer = await service.heartbeat(
                    db,
                    claim.job_id,
                    self.worker_id,
                    lease_seconds=job_type.lease_seconds,
                    progress=progress,
                    note=note,
                )
            if answer:
                context._cancel_requested = True  # noqa: SLF001

        context = JobContext(
            job_id=claim.job_id,
            tenant_id=claim.tenant_id,
            org_id=claim.org_id,
            actor_id=claim.actor_id,
            type=claim.type,
            attempt=claim.attempt,
            payload=payload,
            session=company,
            _progress=beat,
        )
        keeper = asyncio.create_task(self._keep_lease(beat, job_type.lease_seconds))
        try:
            result = await job_type.handler(context)
        except JobCanceled:
            async with company() as db:
                await service.canceled(db, claim.job_id, self.worker_id)
            return
        except JobFailed as exc:
            async with company() as db:
                await service.fail(
                    db,
                    claim.job_id,
                    self.worker_id,
                    code=exc.code,
                    message=str(exc),
                    retry=exc.retry,
                )
            return
        except Exception as exc:
            logger.exception("job %s (%s) failed", claim.job_id, claim.type)
            async with company() as db:
                await service.fail(
                    db,
                    claim.job_id,
                    self.worker_id,
                    code="handler_error",
                    message=type(exc).__name__,
                    retry=True,
                )
            return
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        async with company() as db:
            await service.succeed(db, claim.job_id, self.worker_id, result)

    @staticmethod
    async def _keep_lease(beat: Callable[..., Any], lease_seconds: int) -> None:
        while True:
            await asyncio.sleep(max(lease_seconds / 3, 1))
            try:
                await beat()
            except Exception:
                logger.exception("job lease heartbeat failed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    from app.config import settings

    worker = Worker(
        concurrency=settings.jobs_worker_concurrency, poll_seconds=settings.jobs_poll_seconds
    )

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
        await worker.run_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
