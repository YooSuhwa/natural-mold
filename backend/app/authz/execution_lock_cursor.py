"""Monotonic lock cursor for the canonical execution lock order (parent Task 10).

``Tenant -> sorted Organization -> owner TenantMember -> owner OrganizationMember
-> User KEY SHARE -> sorted Agent lifecycle -> Trigger -> Deployment ->
Conversation -> Run -> Input -> envelope -> queue``. A class may be skipped but
never revisited; within a repeatable class resources are acquired in ascending
UUID-byte order. Run/Input/envelope/queue rows are only ever locked under the
held Conversation row lock, which already serializes them, so neither their
mutual class order nor intra-class UUID order is enforced. Re-locking an exact held resource is
allowed without SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from uuid import UUID

EXPANSION_DENIED = "execution_scope_expansion_denied"


class LockClass(IntEnum):
    TENANT = 0
    ORGANIZATION = 1
    TENANT_MEMBER = 2
    ORGANIZATION_MEMBER = 3
    USER = 4
    AGENT_LIFECYCLE = 5
    TRIGGER = 6
    DEPLOYMENT = 7
    CONVERSATION = 8
    RUN = 9
    INPUT = 10
    ENVELOPE = 11
    QUEUE = 12


class ExecutionProofError(PermissionError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class ExecutionLockCursor:
    _class: LockClass | None = None
    _last: bytes | None = None
    _held: dict[tuple[LockClass, UUID], Any] = field(default_factory=dict)
    _owned: set[tuple[LockClass, UUID]] = field(default_factory=set)

    @property
    def position(self) -> LockClass | None:
        return self._class

    def holds(self, lock_class: LockClass, resource_id: UUID) -> bool:
        return (lock_class, resource_id) in self._held

    def held_row(self, lock_class: LockClass, resource_id: UUID) -> Any:
        return self._held.get((lock_class, resource_id))

    def owns(self, lock_class: LockClass, resource_id: UUID) -> bool:
        return (lock_class, resource_id) in self._owned

    def admit(self, lock_class: LockClass, resource_id: UUID) -> bool:
        """Return ``False`` when ``resource_id`` is already held (skip SQL), else admit.

        Raises :class:`ExecutionProofError` for an earlier class or a
        non-ascending resource inside the current class.
        """

        if self.holds(lock_class, resource_id):
            return False
        current = self._class
        if (
            current is not None
            and lock_class < current
            and not (lock_class >= LockClass.RUN and current >= LockClass.RUN)
        ):
            raise ExecutionProofError(EXPANSION_DENIED)
        if (
            current is not None
            and lock_class == current
            and lock_class < LockClass.RUN
            and self._last is not None
            and resource_id.bytes <= self._last
        ):
            raise ExecutionProofError(EXPANSION_DENIED)
        return True

    def record(self, lock_class: LockClass, resource_id: UUID, row: Any = None) -> None:
        """Record a lock that SQL actually acquired (call only after success)."""

        self._class = lock_class
        self._last = resource_id.bytes
        self._held[(lock_class, resource_id)] = row

    def register_owned(self, lock_class: LockClass, resource_id: UUID, row: Any = None) -> None:
        """Track a row this transaction inserted; it does not move the cursor.

        Re-locking it later still passes through :meth:`admit` ordering unless
        its row is kept (``row``) for SQL-free reuse by the owning proof.
        """

        self._owned.add((lock_class, resource_id))
        if row is not None:
            self._held[(lock_class, resource_id)] = row
