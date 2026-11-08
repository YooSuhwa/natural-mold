"""Generic background jobs (app/jobs): queue rules, the worker, and the requester's API.

The worker runs against the test database with its own session maker; PostgreSQL-only
behaviour (``SKIP LOCKED`` between two workers, RLS) is in
``tests/integration/test_jobs_postgres.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import registry, service
from app.jobs.builtin import PROBE
from app.jobs.context import JobContext
from app.jobs.registry import JobFailed, JobType
from app.jobs.worker import Worker
from app.models.job import Job, JobWorker
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent

CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _types() -> Iterator[None]:
    saved = dict(registry._TYPES)  # noqa: SLF001
    CALLS.clear()
    yield
    registry._TYPES.clear()  # noqa: SLF001
    registry._TYPES.update(saved)  # noqa: SLF001


@pytest.fixture
async def _scope(db: AsyncSession) -> None:
    await seed_agent(db)
    await db.commit()


def _register(name: str, handler: Any, **options: Any) -> None:
    registry.register(JobType(name, handler, **options))


async def _enqueue(job_type: str, **request: Any) -> uuid.UUID:
    async with TestSession() as db:
        job = await service.enqueue(
            db,
            unit_context(TEST_USER_ID),
            service.EnqueueRequest(job_type, request.pop("payload", {}), **request),
            actor_id=TEST_USER_ID,
        )
        await db.commit()
        return job.id


async def _job(job_id: uuid.UUID) -> Job:
    async with TestSession() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


def _worker(name: str = "w1") -> Worker:
    return Worker(worker_id=name, concurrency=2, maker=TestSession)


async def _run_all(worker: Worker) -> None:
    await worker.run_once()
    await worker.drain()


# --- queue ----------------------------------------------------------------------------


@pytest.mark.usefixtures("_scope")
async def test_the_same_key_returns_the_same_job() -> None:
    first = await _enqueue(PROBE, idempotency_key="doc-1")
    again = await _enqueue(PROBE, idempotency_key="doc-1")
    other = await _enqueue(PROBE, idempotency_key="doc-2")

    assert first == again != other


@pytest.mark.usefixtures("_scope")
async def test_a_worker_runs_a_job_to_success_with_progress_and_result() -> None:
    job_id = await _enqueue(PROBE)

    await _run_all(_worker())

    job = await _job(job_id)
    assert (job.state, job.progress, job.attempts) == ("succeeded", 100, 1)
    assert job.result == {"ok": True, "attempt": 1}
    assert job.locked_by is None and job.finished_at is not None


@pytest.mark.usefixtures("_scope")
async def test_a_failure_is_retried_later_then_fails_after_the_last_attempt() -> None:
    async def flaky(context: JobContext) -> None:
        CALLS.append(f"try {context.attempt}")
        raise RuntimeError("boom")

    _register("test.flaky", flaky, max_attempts=2)
    job_id = await _enqueue("test.flaky", max_attempts=2)
    worker = _worker()

    await _run_all(worker)
    queued = await _job(job_id)
    assert (queued.state, queued.attempts, queued.error_code) == ("queued", 1, "handler_error")
    due = service._aware(queued.run_after)  # noqa: SLF001
    assert due is not None and due > service.now()  # backoff
    await _run_all(worker)
    assert CALLS == ["try 1"]  # not due yet

    async with TestSession() as db:
        row = await db.get(Job, job_id)
        assert row is not None
        row.run_after = service.now() - timedelta(seconds=1)
        await db.commit()
    await _run_all(worker)

    failed = await _job(job_id)
    assert CALLS == ["try 1", "try 2"]
    assert (failed.state, failed.attempts) == ("failed", 2)
    assert failed.error_message == "RuntimeError"  # the type only, never the message


@pytest.mark.usefixtures("_scope")
async def test_a_known_failure_without_retry_ends_the_job() -> None:
    async def refuse(_context: JobContext) -> None:
        raise JobFailed("bad_input", "the document is empty", retry=False)

    _register("test.refuse", refuse)
    job_id = await _enqueue("test.refuse")

    await _run_all(_worker())

    job = await _job(job_id)
    assert (job.state, job.attempts, job.error_code) == ("failed", 1, "bad_input")


@pytest.mark.usefixtures("_scope")
async def test_a_job_whose_worker_died_is_taken_again() -> None:
    async def work(context: JobContext) -> dict[str, Any]:
        CALLS.append(f"try {context.attempt}")
        return {"done": True}

    _register("test.work", work)
    job_id = await _enqueue("test.work")
    async with TestSession() as db:
        dead = await service.claim(db, worker_id="dead", types=("test.work",), lease={})
    assert dead is not None and dead.attempt == 1
    async with TestSession() as db:
        row = await db.get(Job, job_id)
        assert row is not None
        row.lease_until = service.now() - timedelta(seconds=1)
        await db.commit()

    await _run_all(_worker("alive"))

    job = await _job(job_id)
    assert CALLS == ["try 2"]
    assert (job.state, job.attempts) == ("succeeded", 2)
    async with TestSession() as db:
        assert not await service.succeed(db, job_id, "dead", {"late": True})
    assert (await _job(job_id)).result == {"done": True}  # the dead worker cannot overwrite


@pytest.mark.usefixtures("_scope")
async def test_an_expired_lease_on_the_last_attempt_fails_instead_of_running_again() -> None:
    _register("test.work", lambda _c: None, max_attempts=1)
    job_id = await _enqueue("test.work", max_attempts=1)
    async with TestSession() as db:
        await service.claim(db, worker_id="dead", types=("test.work",), lease={})
    async with TestSession() as db:
        row = await db.get(Job, job_id)
        assert row is not None
        row.lease_until = service.now() - timedelta(seconds=1)
        await db.commit()

    await _run_all(_worker())

    job = await _job(job_id)
    assert (job.state, job.error_code) == ("failed", "lease_expired")


@pytest.mark.usefixtures("_scope")
async def test_cancel_ends_a_queued_job_and_stops_a_running_one() -> None:
    async def long(context: JobContext) -> None:
        async with TestSession() as db:
            row = await db.get(Job, context.job_id)
            assert row is not None
            await service.request_cancel(db, row)
            await db.commit()
        await context.progress(10)
        context.check_canceled()
        CALLS.append("not reached")

    _register("test.long", long)
    queued = await _enqueue(PROBE)
    running = await _enqueue("test.long")
    async with TestSession() as db:
        row = await db.get(Job, queued)
        assert row is not None
        await service.request_cancel(db, row)
        await db.commit()

    await _run_all(_worker())

    assert (await _job(queued)).state == "canceled"
    assert (await _job(running)).state == "canceled"
    assert CALLS == []


@pytest.mark.usefixtures("_scope")
async def test_the_worker_records_and_clears_its_heartbeat() -> None:
    worker = _worker("beat")
    await worker._beat_worker()  # noqa: SLF001

    async with TestSession() as db:
        row = await db.get(JobWorker, "beat")
        assert row is not None and PROBE in row.job_types
        assert service.heartbeat_age(row) < timedelta(seconds=5)
        await service.forget_worker(db, "beat")
    async with TestSession() as db:
        assert await db.get(JobWorker, "beat") is None


# --- API ------------------------------------------------------------------------------


@pytest.mark.usefixtures("_scope")
async def test_the_requester_reads_follows_and_cancels_their_job(client: AsyncClient) -> None:
    job_id = await _enqueue(PROBE)

    read = await client.get(f"/api/jobs/{job_id}")
    canceled = await client.post(f"/api/jobs/{job_id}/cancel")
    stream = await client.get(f"/api/jobs/{job_id}/events")

    assert read.status_code == 200 and read.json()["state"] == "queued"
    assert canceled.status_code == 200 and canceled.json()["state"] == "canceled"
    assert stream.status_code == 200
    assert '"state":"canceled"' in stream.text and stream.text.startswith("event: job")


async def test_someone_elses_job_is_not_found(client: AsyncClient, db: AsyncSession) -> None:
    other = uuid.uuid4()
    await seed_agent(db, user_id=other)
    await db.commit()
    async with TestSession() as session:
        job = await service.enqueue(
            session, unit_context(other), service.EnqueueRequest(PROBE, {}), actor_id=other
        )
        await session.commit()

    for response in (
        await client.get(f"/api/jobs/{job.id}"),
        await client.get(f"/api/jobs/{job.id}/events"),
        await client.post(f"/api/jobs/{job.id}/cancel"),
        await client.get(f"/api/jobs/{uuid.uuid4()}"),
    ):
        assert response.status_code == 404
    async with TestSession() as session:
        assert (await session.scalar(select(Job.state).where(Job.id == job.id))) == "queued"


async def test_the_worker_process_starts_the_checkpointer_and_hooks_for_agent_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow agent steps run in the worker process; without these they all failed."""
    from app.agent_runtime import checkpointer
    from app.hooks import hooks
    from app.jobs import worker as worker_module

    calls: list[str] = []

    async def init(*_args: Any, **_kwargs: Any) -> None:
        calls.append("init")

    async def shutdown() -> None:
        calls.append("shutdown")

    class StubWorker:
        async def run_forever(self) -> None:
            calls.append("run")
            names = {hook.name for hook in hooks.all()}
            assert names >= {"logging_hook", "audit_hook", "spend_hook"}

    monkeypatch.setattr(checkpointer, "init_checkpointer", init)
    monkeypatch.setattr(checkpointer, "shutdown_checkpointer", shutdown)
    monkeypatch.setattr(hooks, "_hooks", [])
    await worker_module.serve(StubWorker())  # type: ignore[arg-type]
    assert calls == ["init", "run", "shutdown"]
