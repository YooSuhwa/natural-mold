"""Company-keyed in-memory queue of skill evaluation runs (plan 8.8 queue items, H-07).

Every queue item names its company explicitly: ``tenant_id`` is the evaluated skill's
company, ``None`` only for a system skill (platform scope). The worker opens one session
per item for that company and the run's requester, so no execution mixes companies.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field


class SkillEvaluationQueueFull(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillEvaluationQueueItem:
    run_id: uuid.UUID
    tenant_id: uuid.UUID | None
    user_id: uuid.UUID

    def __post_init__(self) -> None:
        if self.tenant_id is not None and not isinstance(self.tenant_id, uuid.UUID):
            raise TypeError("skill evaluation queue items require a tenant_id (or None)")
        if not isinstance(self.user_id, uuid.UUID):
            raise TypeError("skill evaluation queue items require the requester user_id")


@dataclass(slots=True)
class SkillEvaluationQueue:
    queue_max_size: int = 100
    _queue: deque[SkillEvaluationQueueItem] = field(default_factory=deque, init=False)
    _queued_ids: set[uuid.UUID] = field(default_factory=set, init=False)
    _reserved_slots: int = field(default=0, init=False)
    _wake: asyncio.Event | None = field(default=None, init=False)

    def reserve_slot(self) -> None:
        if len(self._queue) + self._reserved_slots >= self.queue_max_size:
            raise SkillEvaluationQueueFull("skill evaluation queue is full")
        self._reserved_slots += 1

    def release_slot(self) -> None:
        self._reserved_slots = max(self._reserved_slots - 1, 0)

    def enqueue(
        self,
        run_id: uuid.UUID,
        *,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID,
        reserved: bool = False,
    ) -> None:
        item = SkillEvaluationQueueItem(run_id, tenant_id, user_id)
        if run_id in self._queued_ids:
            if reserved:
                self.release_slot()
            return
        if not reserved and len(self._queue) + self._reserved_slots >= self.queue_max_size:
            raise SkillEvaluationQueueFull("skill evaluation queue is full")
        if reserved:
            self.release_slot()
        self._queue.append(item)
        self._queued_ids.add(run_id)
        if self._wake is not None:
            self._wake.set()

    def pop_next(self) -> SkillEvaluationQueueItem | None:
        if not self._queue:
            return None
        item = self._queue.popleft()
        self._queued_ids.discard(item.run_id)
        return item
