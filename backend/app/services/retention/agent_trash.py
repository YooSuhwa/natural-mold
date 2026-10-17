"""Permanently purge agents whose trash period ran out (plan D19, Q12, 10.7).

The purge is the reviewed B-08 pipeline (``purge_agent``): retained history is
materialized first and every conversation stays as a read-only record with the
agent's name at that time. That pipeline authorizes a live org administrator (its
B-08 files are hash-preserved, so no system actor can be added there), so the sweep
borrows the organization's first effective administrator (lowest id, deterministic),
never their super-user flag. The ``agent.purge`` event carries ``retention_sweep`` and
``actor_kind: system`` in its metadata. An organization without an administrator keeps
its trash, and the sweep records a skipped ``retention.purge`` warning event.
"""

import logging
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz import audit_actions
from app.authz.agent_lifecycle_scope import PURGE_AUDIT_METADATA
from app.authz.org_administrators import active_administrators
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.organization import Organization
from app.models.user import User
from app.services import audit_service
from app.services.agent_purge_service import purge_agent
from app.services.agent_purge_types import AgentPurgeCommand

logger = logging.getLogger(__name__)


async def purge_actor(db: AsyncSession, org_id: UUID) -> CurrentUser | None:
    organization = await db.get(Organization, org_id)
    if organization is None:
        return None
    administrators = sorted(await active_administrators(db, organization), key=str)
    if not administrators:
        return None
    user = await db.get(User, administrators[0])
    if user is None:
        return None
    return CurrentUser(id=user.id, email=user.email, name=user.name)  # never super-user


async def _record_no_administrator(db: AsyncSession, org_id: UUID, waiting: int) -> None:
    organization = await db.get(Organization, org_id)
    if organization is None:
        return
    await audit_service.record_event(
        db,
        actor_type="system",
        action=audit_actions.RETENTION_PURGE,
        target_type="organization",
        target_id=org_id,
        target_name_snapshot=organization.name,
        outcome="skipped",
        reason_code="no_org_administrator",
        tenant_id=organization.tenant_id,
        metadata={"agents_waiting": waiting},
    )
    await db.commit()


async def purge_expired_agents(
    db: AsyncSession,
    org_id: UUID,
    expired: Select[tuple[UUID]],
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> int:
    """Purge each agent still selected by ``expired``; a failure is retried next sweep."""
    agent_ids = list(await db.scalars(expired.order_by(Agent.id)))
    if not agent_ids:
        return 0
    actor = await purge_actor(db, org_id)
    if actor is None:
        logger.warning("retention: no administrator to purge trash of org %s", org_id)
        await _record_no_administrator(db, org_id, len(agent_ids))
        return 0
    marker = {"retention_sweep": True, "actor_kind": "system", "acting_admin_id": str(actor.id)}
    purged = 0
    for agent_id in agent_ids:
        name = await db.scalar(expired.with_only_columns(Agent.name).where(Agent.id == agent_id))
        if name is None:  # restored, re-archived or already gone since the listing
            continue
        command = AgentPurgeCommand(db, actor, agent_id, name, checkpointer)
        token = PURGE_AUDIT_METADATA.set(marker)
        try:
            receipt = await purge_agent(command)
        except Exception:  # noqa: BLE001 - e.g. a run still stopping; retried next sweep
            await db.rollback()
            logger.warning("retention: agent %s purge deferred", agent_id, exc_info=True)
            continue
        finally:
            PURGE_AUDIT_METADATA.reset(token)
        purged += int(receipt.state != "preparing")  # the agent row is gone
    return purged
