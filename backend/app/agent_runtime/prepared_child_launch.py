"""One-shot child-launch admission for prepared leases (parent Task 10, I12).

``PreparedChildLaunchAdmission`` is a typed ``orchestration_admission``
operation under the ``live_application_resource`` descriptor class. Each
reservation opens a fresh canonical dispatch permit (``kind="child_launch"``),
revalidates the exact catalog child token and digest, and records exactly one
``(lease_id, parent_task_id, child_id, invocation_id)`` winner in a
process-local ledger. The permit transaction is closed before the reservation
is returned, so no authority lock is held while the child runs; the
reservation carries no session or proof. A duplicate loses without launch,
cancellation before launch releases the slot, and ledger terminalization
rejects new reservations, cancels launched children exactly once and purges
the run's slots. I20/I22 (lease terminalization) must call
``get_child_launch_ledger().terminalize(run_id)``; nothing else purges slots.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agent_runtime.lease_dispatch_permit import (
    DispatchDeniedError,
    SessionFactory,
    open_dispatch_permit,
)
from app.agent_runtime.prepared_subagent_catalog import PreparedChildCatalog
from app.agent_runtime.runtime_config import PreparedRunLease

DESCRIPTOR_CLASS = "live_application_resource"
OPERATION_KIND = "orchestration_admission"
CHILD_NOT_PREPARED = "child_launch_not_prepared"
CHILD_DUPLICATE = "child_launch_duplicate"
LEASE_TERMINAL = "prepared_run_lease_missing"
# Closed lease IDs kept to reject late reservations; bounded so a long-lived worker
# does not grow without limit (I24 L1). An evicted (old) lease is still refused by
# the registry/permit, which no longer holds it.
CLOSED_LEASE_CAP = 4096


def child_launch_descriptor(child_id: str) -> str:
    return f"child_launch:{child_id}"


def child_launch_descriptors(catalog: PreparedChildCatalog) -> frozenset[str]:
    return frozenset(child_launch_descriptor(child_id) for child_id in catalog.children)


@dataclass(frozen=True, slots=True)
class ChildLaunchKey:
    lease_id: uuid.UUID
    parent_task_id: str
    child_id: str
    invocation_id: str


class ChildLaunchState(StrEnum):
    RESERVED = "reserved"
    LAUNCHED = "launched"
    FINISHED = "finished"


@dataclass(eq=False, slots=True)
class _Slot:
    state: ChildLaunchState
    task: asyncio.Task[Any] | None = None


class ChildLaunchLedger:
    """Process-local winners; mutated only on the event loop without awaits."""

    def __init__(self, closed_cap: int = CLOSED_LEASE_CAP) -> None:
        self._slots: dict[ChildLaunchKey, _Slot] = {}
        self._closed: OrderedDict[uuid.UUID, None] = OrderedDict()
        self._closed_cap = closed_cap

    def state(self, key: ChildLaunchKey) -> ChildLaunchState | None:
        slot = self._slots.get(key)
        return None if slot is None else slot.state

    def ensure_open(self, lease_id: uuid.UUID) -> None:
        if lease_id in self._closed:
            raise DispatchDeniedError(LEASE_TERMINAL)

    def claim(self, key: ChildLaunchKey) -> bool:
        self.ensure_open(key.lease_id)
        if key in self._slots:
            return False
        self._slots[key] = _Slot(ChildLaunchState.RESERVED)
        return True

    def release(self, key: ChildLaunchKey) -> None:
        slot = self._slots.get(key)
        if slot is not None and slot.state is ChildLaunchState.RESERVED:
            del self._slots[key]

    def launch(
        self, key: ChildLaunchKey, start: Callable[[], Coroutine[Any, Any, Any]]
    ) -> asyncio.Task[Any]:
        slot = self._slots.get(key)
        if slot is None or slot.state is not ChildLaunchState.RESERVED:
            raise DispatchDeniedError(CHILD_NOT_PREPARED)
        if key.lease_id in self._closed:
            del self._slots[key]
            raise DispatchDeniedError(LEASE_TERMINAL)
        slot.state = ChildLaunchState.LAUNCHED
        slot.task = asyncio.ensure_future(start())
        return slot.task

    def finish(self, key: ChildLaunchKey) -> bool:
        slot = self._slots.get(key)
        if slot is None or slot.state is ChildLaunchState.FINISHED:
            return False
        if slot.state is ChildLaunchState.RESERVED:
            del self._slots[key]
            return True
        slot.state, slot.task = ChildLaunchState.FINISHED, None
        return True

    def is_closed(self, lease_id: uuid.UUID) -> bool:
        return lease_id in self._closed

    def terminalize(self, lease_id: uuid.UUID) -> int:
        """Reject new reservations, cancel each launched child once, purge the run's slots.

        The closed lease ID alone keeps rejecting reservations, so no slot of a
        terminalized run is retained. Lease terminalization paths (I20/I22)
        must call this exactly when the prepared run terminalizes.
        """

        self._closed[lease_id] = None
        self._closed.move_to_end(lease_id)
        while len(self._closed) > self._closed_cap:
            self._closed.popitem(last=False)
        cancelled = 0
        for key, slot in list(self._slots.items()):
            if key.lease_id != lease_id:
                continue
            if slot.task is not None and not slot.task.done():
                slot.task.cancel()
                cancelled += 1
            del self._slots[key]
        return cancelled


@dataclass(eq=False, slots=True)
class ChildLaunchReservation:
    """The won slot. It grants no provider/tool/backend authority."""

    key: ChildLaunchKey
    _ledger: ChildLaunchLedger = field(repr=False)

    def launch(self, start: Callable[[], Coroutine[Any, Any, Any]]) -> asyncio.Task[Any]:
        return self._ledger.launch(self.key, start)

    def release(self) -> None:
        self._ledger.release(self.key)

    def finish(self) -> bool:
        return self._ledger.finish(self.key)

    @property
    def terminalized(self) -> bool:
        return self._ledger.is_closed(self.key.lease_id)


class PreparedChildLaunchAdmission:
    descriptor_class = DESCRIPTOR_CLASS
    operation_kind = OPERATION_KIND

    def __init__(
        self,
        lease: PreparedRunLease,
        *,
        catalog: PreparedChildCatalog,
        catalog_digest: str,
        allowed_descriptors: frozenset[str],
        session_factory: SessionFactory,
        ledger: ChildLaunchLedger | None = None,
    ) -> None:
        self._lease = lease
        self._catalog = catalog
        self._catalog_digest = catalog_digest
        self._allowed = allowed_descriptors
        self._session_factory = session_factory
        self._ledger = ledger or get_child_launch_ledger()

    async def reserve_child_launch(
        self,
        *,
        parent_task_id: str,
        parent_path: str,
        child_id: str,
        child_token: str,
        invocation_id: str,
    ) -> ChildLaunchReservation:
        """Reserve one launch; ``parent_task_id`` is the invoking task path."""

        child = self._catalog.children.get(child_id)
        if (
            child is None
            or child.parent_path != parent_path
            or not parent_task_id
            or not secrets.compare_digest(child.token, child_token)
            or not secrets.compare_digest(self._catalog.digest, self._catalog_digest)
            or not invocation_id
        ):
            raise DispatchDeniedError(CHILD_NOT_PREPARED)
        key = ChildLaunchKey(self._lease.binding.run_id, parent_task_id, child_id, invocation_id)
        self._ledger.ensure_open(key.lease_id)
        claimed = False
        try:
            async with open_dispatch_permit(
                self._lease,
                kind="child_launch",
                descriptor_id=child_launch_descriptor(child_id),
                allowed_descriptors=self._allowed,
                session_factory=self._session_factory,
            ):
                claimed = self._ledger.claim(key)
                if not claimed:
                    raise DispatchDeniedError(CHILD_DUPLICATE)
        except BaseException:
            if claimed:
                self._ledger.release(key)
            raise
        return ChildLaunchReservation(key, self._ledger)


_ledger = ChildLaunchLedger()


def get_child_launch_ledger() -> ChildLaunchLedger:
    return _ledger
