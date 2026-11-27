"""Bind a linked chat child's control transactions to its immutable prepared lease."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import (
    DispatchDeniedError,
    SessionFactory,
    open_dispatch_permit,
)
from app.agent_runtime.prepared_subagent_catalog import PreparedChildCatalog
from app.agent_runtime.runtime_config import PreparedRunLease
from app.authz.org_context import OrgContext
from app.database import tenant_session

if TYPE_CHECKING:
    from app.agent_runtime.delegated_child_control import ChildTaskLifecycle


@dataclass(frozen=True, slots=True)
class _ControlAuthority:
    lease: PreparedRunLease
    child_agent_id: UUID
    context: OrgContext


_CONTROL_AUTHORITY = "moldy_child_control_authority"


def lease_for_child_control(
    db: AsyncSession, *, context: OrgContext, run_id: UUID, child_agent_id: UUID
) -> PreparedRunLease | None:
    """Expose the exact live worker lease to its single child-control transaction."""
    authority = db.info.get(_CONTROL_AUTHORITY)
    if authority is None:
        return None
    if not isinstance(authority, _ControlAuthority):
        raise DispatchDeniedError("child_control_scope_mismatch")
    if (
        authority.lease.binding.run_id != run_id
        or authority.context != context
        or authority.child_agent_id != child_agent_id
    ):
        raise DispatchDeniedError("child_control_scope_mismatch")
    return authority.lease


def child_control_descriptor(path: str, agent_id: UUID) -> str:
    return f"child_control:{path}:{agent_id}"


@asynccontextmanager
async def _control_session(
    lease: PreparedRunLease,
    descriptor: str,
    child_agent_id: UUID,
    context: OrgContext,
    session_factory: SessionFactory,
) -> AsyncIterator[AsyncSession]:
    # The permit transaction closes before opening the tenant transaction.
    # This scope checks authority and records control state, never executes tools.
    async with (
        open_dispatch_permit(
            lease,
            kind="child_control",
            descriptor_id=descriptor,
            allowed_descriptors=lease.descriptor_ids,
            session_factory=session_factory,
        ),
        tenant_session(context, maker=session_factory) as db,
    ):
        if _CONTROL_AUTHORITY in db.info:
            raise DispatchDeniedError("child_control_scope_mismatch")
        db.info[_CONTROL_AUTHORITY] = _ControlAuthority(lease, child_agent_id, context)
        try:
            yield db
        finally:
            del db.info[_CONTROL_AUTHORITY]


def prepared_child_controls(
    agents: Mapping[str, UUID],
    *,
    lease: PreparedRunLease,
    catalog: PreparedChildCatalog,
    session_factory: SessionFactory,
) -> dict[str, ChildTaskLifecycle]:
    if not agents:
        return {}
    from app.agent_runtime.delegated_child_control import ChildTaskLifecycle

    binding = lease.binding
    context = OrgContext(
        org_id=binding.org_id, tenant_id=binding.tenant_id, user_id=binding.user_id
    )
    controls = {}
    for path, agent_id in agents.items():
        child = catalog.children.get(path)
        descriptor = child_control_descriptor(path, agent_id)
        if child is None or child.origin != "declarative" or descriptor not in lease.descriptor_ids:
            raise DispatchDeniedError("child_control_not_prepared")
        controls[path] = ChildTaskLifecycle(
            context=context,
            conversation_id=binding.conversation_id,
            child_agent_id=agent_id,
            role=child.name,
            session_scope=partial(
                _control_session, lease, descriptor, agent_id, context, session_factory
            ),
        )
    return controls
