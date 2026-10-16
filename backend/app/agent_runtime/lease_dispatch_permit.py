"""Per-dispatch authority permit for prepared leases (parent Task 10, internal I12).

Before every provider send, tool dispatch, backend call and child launch, a
fresh short transaction takes the canonical parent locks (Tenant ->
Organization -> owner TenantMember -> owner OrganizationMember -> User) and
requires an active Tenant and both active owner memberships for the lease's
exact binding. Only Organization ``archived`` is bypassed, and only for the
exact live lease. The permit stays open (locks held) for the duration of the
dispatched leaf, so archive and membership mutation have a deterministic
winner; it never carries a session into the leaf and is closed before any
child graph is awaited.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    LeaseState,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.agent_runtime.runtime_config import PreparedRunLease
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.exceptions import AppError

PermitKind = Literal["provider", "tool", "backend", "child_launch", "summary"]
SessionFactory = Callable[[], AsyncSession]

DESCRIPTOR_NOT_PREPARED = "descriptor_not_prepared"


class DispatchDeniedError(PermissionError):
    """A leaf dispatch was refused; the graph must terminalize without the call."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    kind: PermitKind
    descriptor_id: str
    archived_bypass: bool


def _context(lease: PreparedRunLease) -> OrgContext:
    binding = lease.binding
    return OrgContext(org_id=binding.org_id, tenant_id=binding.tenant_id, user_id=binding.user_id)


@asynccontextmanager
async def open_dispatch_permit(
    lease: PreparedRunLease,
    *,
    kind: PermitKind,
    descriptor_id: str,
    allowed_descriptors: frozenset[str],
    session_factory: SessionFactory,
) -> AsyncIterator[DispatchPermit]:
    """Hold fresh canonical authority for exactly one leaf dispatch."""

    if descriptor_id not in allowed_descriptors:
        raise DispatchDeniedError(DESCRIPTOR_NOT_PREPARED)
    registry = get_prepared_run_registry()
    if registry.state(lease.binding.run_id) not in (LeaseState.ACTIVE, LeaseState.DISPATCHING):
        raise DispatchDeniedError("prepared_run_lease_missing")
    async with session_factory() as db:
        try:
            proof = await acquire_execution_proof(db, _context(lease), lease=lease)
            registry.acquire(lease.binding, token=lease.token, digest=lease.digest)
        except AppError as error:
            await db.rollback()
            code = (
                "organization_archived" if error.code == "organization_unavailable" else error.code
            )
            raise DispatchDeniedError(code) from None
        except (ExecutionProofError, PreparedRunLeaseError) as error:
            await db.rollback()
            raise DispatchDeniedError(error.code) from None
        try:
            yield DispatchPermit(kind, descriptor_id, proof.archived_bypass)
        finally:
            await db.rollback()
