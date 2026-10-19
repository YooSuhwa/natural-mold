"""H-06 background fairness: one company does not monopolize the evaluation worker (8.7)."""

import asyncio
import uuid

import pytest

from app.services.skill_evaluation_queue import SkillEvaluationQueue, SkillEvaluationQueueItem
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.tenancy.fair_share import fair_index

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
USER = uuid.uuid4()


def test_fair_index_prefers_the_least_busy_company_then_the_oldest_item() -> None:
    assert fair_index([], {}) == -1
    assert fair_index([A, A, B], {}) == 0
    assert fair_index([A, A, B, C], {A: 2, B: 1, C: 1}) == 2
    assert fair_index([A, A], {A: 5}) == 0  # alone in the queue: still served


def test_queue_interleaves_companies_while_items_run() -> None:
    queue = SkillEvaluationQueue(queue_max_size=10)
    runs = {uuid.uuid4(): tenant for tenant in (A, A, A, B, B, None)}
    for run_id, tenant in runs.items():
        queue.enqueue(run_id, tenant_id=tenant, user_id=USER)
    first = [queue.pop_next() for _ in range(3)]
    assert [item.tenant_id for item in first if item] == [A, B, None]
    assert first[1] is not None
    queue.finish(first[1])  # B's run finished: B is now the least busy
    assert (item := queue.pop_next()) is not None and item.tenant_id == B
    assert (item := queue.pop_next()) is not None and item.tenant_id == A
    assert (item := queue.pop_next()) is not None and item.tenant_id == A
    assert queue.pop_next() is None


class RecordingWorker(SkillEvaluationWorker):
    executed: list[uuid.UUID | None]
    gate: asyncio.Event

    async def _execute_run_with_session(self, item: SkillEvaluationQueueItem) -> None:
        self.executed.append(item.tenant_id)
        await self.gate.wait()


def _worker() -> RecordingWorker:
    worker = RecordingWorker(queue_max_size=10, max_concurrent=1)
    worker.executed, worker.gate = [], asyncio.Event()
    return worker


async def _settle(worker: RecordingWorker, count: int) -> None:
    for _ in range(200):
        if len(worker.executed) >= count:
            return
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_worker_releases_the_company_slot_when_a_run_ends() -> None:
    worker = _worker()
    assert await worker.start(session_factory=lambda: None)  # type: ignore[arg-type,return-value]
    for tenant in (A, A, B):
        worker.enqueue(uuid.uuid4(), tenant_id=tenant, user_id=USER)
    await _settle(worker, 1)
    assert dict(worker._running) == {A: 1}
    worker.gate.set()
    await _settle(worker, 3)
    await worker.stop()
    assert worker.executed == [A, A, B] and dict(worker._running) == {}


@pytest.mark.asyncio
async def test_a_cancelled_run_never_leaks_its_company_slot() -> None:
    worker = _worker()
    assert await worker.start(session_factory=lambda: None)  # type: ignore[arg-type,return-value]
    worker.enqueue(uuid.uuid4(), tenant_id=A, user_id=USER)
    await _settle(worker, 1)
    await worker.stop(timeout_seconds=0.01)  # cancels the blocked run
    assert dict(worker._running) == {}
