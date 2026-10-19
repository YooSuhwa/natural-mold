"""Process-local registry of prepared run leases (parent Task 10).

States: ``reserved -> pending_commit -> active [-> dispatching] -> terminalizing
-> removed``. ``claim_dispatch`` is the single-use ``active -> dispatching``
transition the runner takes; a dispatching lease stays live for permits.
A pending lease is never handed out; activation after the materialization
commit requires the exact token and digest, and ``acquire`` the exact binding,
token and digest. A persisted materialization marker without an active lease
maps to ``prepared_run_lease_missing``: removed run IDs are retired, so a run
can never be re-reserved and rebuilt. Every lease the registry receives —
including one rejected by ``stage`` — is closed exactly once, in a task that
caller cancellation cannot interrupt and that is abandoned at the timeout.

This is deliberately separate from ``RunTaskRegistry`` (task bookkeeping).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease

logger = logging.getLogger(__name__)

CleanupOutcome = Literal["closed", "no_lease", "close_failed", "close_timeout", "already_removed"]

LEASE_MISSING = "prepared_run_lease_missing"
LEASE_MISMATCH = "prepared_run_lease_mismatch"
LEASE_DUPLICATE = "prepared_run_lease_duplicate"
_RETIRED_LIMIT = 65536


class PreparedRunLeaseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LeaseState(StrEnum):
    RESERVED = "reserved"
    PENDING_COMMIT = "pending_commit"
    ACTIVE = "active"
    DISPATCHING = "dispatching"
    TERMINALIZING = "terminalizing"


_LIVE = (LeaseState.ACTIVE, LeaseState.DISPATCHING)


@dataclass(eq=False, slots=True)
class _Entry:
    binding: PreparedRunBinding
    token: str
    state: LeaseState
    lease: PreparedRunLease | None = None
    digest: str | None = None
    cleanup: asyncio.Task[CleanupOutcome] | None = None


class PreparedRunRegistry:
    def __init__(self, *, cleanup_timeout_seconds: float = 5.0) -> None:
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._entries: dict[uuid.UUID, _Entry] = {}
        self._retired: OrderedDict[uuid.UUID, None] = OrderedDict()
        self._abandoned: set[asyncio.Future[None]] = set()

    def state(self, run_id: uuid.UUID) -> LeaseState | None:
        entry = self._entries.get(run_id)
        return None if entry is None else entry.state

    def active_run_ids(self) -> set[uuid.UUID]:
        return {key for key, entry in self._entries.items() if entry.state in _LIVE}

    def reserve(self, binding: PreparedRunBinding) -> str:
        if binding.run_id in self._entries or binding.run_id in self._retired:
            raise PreparedRunLeaseError(LEASE_DUPLICATE)
        token = secrets.token_urlsafe(32)
        self._entries[binding.run_id] = _Entry(binding, token, LeaseState.RESERVED)
        return token

    async def stage(self, lease: PreparedRunLease) -> None:
        """Attach a built lease to its reservation, or close it and raise."""

        entry = self._entries.get(lease.binding.run_id)
        code: str | None = None
        if (
            entry is None
            or entry.state is not LeaseState.RESERVED
            or entry.binding != lease.binding
        ):
            code = LEASE_MISSING
        elif not secrets.compare_digest(entry.token, lease.token):
            code = LEASE_MISMATCH
        if code is not None:
            await self._close(lease.binding.run_id, lease)
            raise PreparedRunLeaseError(code)
        assert entry is not None  # noqa: S101 - narrowed by the rejection branch
        entry.lease = lease
        entry.digest = lease.digest
        entry.state = LeaseState.PENDING_COMMIT

    def activate(self, run_id: uuid.UUID, *, token: str, digest: str) -> None:
        entry = self._entry(run_id, LeaseState.PENDING_COMMIT)
        self._require_exact(entry, token=token, digest=digest)
        entry.state = LeaseState.ACTIVE

    def acquire(self, binding: PreparedRunBinding, *, token: str, digest: str) -> PreparedRunLease:
        entry = self._entry(binding.run_id, *_LIVE)
        if entry.binding != binding:
            raise PreparedRunLeaseError(LEASE_MISMATCH)
        return self._require_exact(entry, token=token, digest=digest)

    def claim_dispatch(
        self, binding: PreparedRunBinding, *, token: str, digest: str
    ) -> PreparedRunLease:
        """Single-use ``active -> dispatching``; a second claim is ``lease_missing``."""

        entry = self._entry(binding.run_id, LeaseState.ACTIVE)
        if entry.binding != binding:
            raise PreparedRunLeaseError(LEASE_MISMATCH)
        lease = self._require_exact(entry, token=token, digest=digest)
        entry.state = LeaseState.DISPATCHING
        return lease

    def ensure_live_for_marker(self, run_id: uuid.UUID, *, marker_digest: str) -> None:
        """Fail closed unless a persisted marker has its exact live lease.

        Returns nothing: the persisted row alone never yields the lease.
        """

        entry = self._entry(run_id, *_LIVE)
        if entry.digest is None or not secrets.compare_digest(entry.digest, marker_digest):
            raise PreparedRunLeaseError(LEASE_MISMATCH)

    async def terminalize(self, run_id: uuid.UUID) -> CleanupOutcome:
        entry = self._entries.get(run_id)
        if entry is None:
            return "already_removed"
        if entry.cleanup is None:
            entry.state = LeaseState.TERMINALIZING
            entry.cleanup = asyncio.ensure_future(self._cleanup(run_id, entry))
        return await asyncio.shield(entry.cleanup)

    def _entry(self, run_id: uuid.UUID, *states: LeaseState) -> _Entry:
        entry = self._entries.get(run_id)
        if entry is None or entry.state not in states:
            raise PreparedRunLeaseError(LEASE_MISSING)
        return entry

    @staticmethod
    def _require_exact(entry: _Entry, *, token: str, digest: str) -> PreparedRunLease:
        lease = entry.lease
        if (
            lease is None
            or entry.digest is None
            or not secrets.compare_digest(entry.token, token)
            or not secrets.compare_digest(entry.digest, digest)
        ):
            raise PreparedRunLeaseError(LEASE_MISMATCH)
        return lease

    async def _cleanup(self, run_id: uuid.UUID, entry: _Entry) -> CleanupOutcome:
        try:
            if entry.lease is None:
                return "no_lease"
            return await self._close(run_id, entry.lease)
        finally:
            entry.lease = None
            if self._entries.get(run_id) is entry:
                del self._entries[run_id]
            self._retire(run_id)

    async def _close(self, run_id: uuid.UUID, lease: PreparedRunLease) -> CleanupOutcome:
        if lease.close is None:
            return "no_lease"
        task = asyncio.ensure_future(lease.close())
        done, _pending = await asyncio.wait({task}, timeout=self._cleanup_timeout_seconds)
        if not done:
            task.cancel()
            self._abandoned.add(task)
            task.add_done_callback(self._abandoned.discard)
            logger.warning("prepared run lease cleanup timed out run_id=%s", run_id)
            return "close_timeout"
        if task.cancelled() or task.exception() is not None:
            error = "CancelledError" if task.cancelled() else type(task.exception()).__name__
            logger.warning("prepared run lease cleanup failed run_id=%s error=%s", run_id, error)
            return "close_failed"
        return "closed"

    def _retire(self, run_id: uuid.UUID) -> None:
        self._retired[run_id] = None
        while len(self._retired) > _RETIRED_LIMIT:
            self._retired.popitem(last=False)


_registry = PreparedRunRegistry()


def get_prepared_run_registry() -> PreparedRunRegistry:
    return _registry


def reset_prepared_run_registry_for_tests(registry: PreparedRunRegistry | None = None) -> None:
    global _registry
    _registry = registry or PreparedRunRegistry()
