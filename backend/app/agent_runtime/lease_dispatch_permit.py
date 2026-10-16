"""Per-dispatch authority permit for prepared leases (parent Task 10, internal I12).

Before every provider send, tool dispatch, backend call and child launch, a
fresh short transaction takes the canonical parent locks (Tenant ->
Organization -> owner TenantMember -> owner OrganizationMember -> User) and
requires an active Tenant and both active owner memberships for the lease's
exact binding. Only Organization ``archived`` is bypassed, and only for the
exact live lease.

Order is lock -> decide -> record -> release -> I/O: the authority
transaction is rolled back (it only read) and its session/pooled connection
returned *before* the context manager yields, so no row lock, transaction or
connection is held across the dispatched leaf (provider HTTP send, tool call,
child launch). The yielded :class:`DispatchPermit` carries no session.

Parent-contract interpretation ("a serialized permit under these locks so
archive and membership mutation have a deterministic winner"): the winner is
decided at the permit's lock acquisition. A Tenant suspension, membership
revocation or archive committed before the permit acquires its locks is
observed by it (deny, or exact-lease archive bypass); one committed after the
permit released its locks is not visible to the in-flight leaf, which
completes, and is observed by the very next dispatch boundary. Writers
(archive, suspension, membership mutation) therefore wait at most one short
authority transaction, never a provider/tool call.
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
    """Prove fresh canonical authority, release it, then admit exactly one leaf."""

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
        permit = DispatchPermit(kind, descriptor_id, proof.archived_bypass)
        await db.rollback()  # read-only proof: release every lock before the leaf
    yield permit
