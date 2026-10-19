"""OpenFGA side of an Agent purge (plan 7.2.1 "자원 삭제", 8.6).

Kept outside the hash-preserved B-08 purge modules: ``purge_agent`` queues the tuple
deletes (plus the ``resource`` deny marker) in the same transaction that
``finalize_agent_purge`` deletes the Agent row in, and applies them after commit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle, on_resource_deleted
from app.authz.runtime import runtime_authz
from app.authz.service_types import AuthzMode
from app.models.agent import Agent


async def queue_agent_tuple_deletes(
    db: AsyncSession, agent_id: UUID, *, mode: AuthzMode | None = None
) -> LifecycleChange:
    """Outside legacy mode only; legacy runs no query at all."""
    mode = mode or runtime_authz().mode
    if mode == "legacy":
        return LifecycleChange()
    row = await db.get(Agent, agent_id)
    if row is None:
        return LifecycleChange()
    return await on_resource_deleted(db, row, mode=mode)


async def apply_agent_tuple_deletes(change: LifecycleChange) -> None:
    """After commit; failures are left to the C-05 drain (the marker already denies)."""
    await apply_lifecycle(change, runtime_authz())
