"""One company's ``retention_sweep`` pass (plan 6.2, 10.7, 14.1; E-08).

Per organization (active or suspended; archived organizations stay frozen until the
organization purge contract exists): idle conversations past ``conversation_days``
(``idle.idle_before``), the removed members' conversations and memories past
``removed_member_days`` (each conversation is deleted under the member row lock with
the member predicate re-checked, so a re-add during the sweep keeps everything not yet
deleted, and the data comes back with the membership), then agents in the trash past
``agent_trash_days``. Per company: audit rows past ``audit_retention_days``; no action
is exempt, ``retention.purge`` rows age out like the rest. Bulk deletes run in chunks
of ``CHUNK`` ids. Each non-empty result is recorded as ``retention.purge`` with the
counts. Leadership is re-checked before each organization.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz import audit_actions
from app.authz.tenant_org_write_locks import lock_member_rows
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.organization import Organization, Tenant
from app.schemas.retention import RetentionPreview, audit_retention_days, org_retention
from app.services import audit_service
from app.services.retention import targets
from app.services.retention.agent_trash import purge_expired_agents
from app.services.retention.conversations import PreLock, delete_conversations

CHUNK: Final = 1000
Leadership = Callable[[], Awaitable[bool]]


async def always_leader() -> bool:
    return True


@dataclass(frozen=True, slots=True)
class OrgRef:
    """Detached ids: per-row commits and rollbacks expire ORM instances."""

    id: UUID
    tenant_id: UUID
    name: str
    settings: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SweepRequest:
    db: AsyncSession
    now: datetime
    checkpointer: BaseCheckpointSaver[str] | None = None
    still_leader: Leadership = always_leader


async def _ids(db: AsyncSession, query: Select[tuple[UUID]]) -> list[UUID]:
    return list(await db.scalars(query.order_by(*query.selected_columns)))


async def delete_in_chunks(
    db: AsyncSession, model: type[AuditEvent | MemoryRecord | MemoryProposal], query: Select
) -> int:
    """Delete what ``query`` (a ``model.id`` selection) selects, ``CHUNK`` ids per commit.

    The delete statement re-applies ``query``, so a row that stopped matching between
    the chunk read and the delete (a re-added member's memory) is kept.
    """
    deleted = 0
    while ids := await _ids(db, query.limit(CHUNK)):
        result = await db.execute(delete(model).where(model.id.in_(ids), model.id.in_(query)))
        await db.commit()
        removed = int(getattr(result, "rowcount", 0) or 0)
        if removed == 0:
            break
        deleted += removed
    return deleted


def _member_prelock(org: OrgRef) -> PreLock:
    """Member rows before the conversation row, the member-write lock order."""

    async def prelock(db: AsyncSession, conversation_id: UUID) -> None:
        owner_of = select(Conversation.user_id).where(Conversation.id == conversation_id)
        owner = await db.scalar(owner_of)
        if owner is not None:
            await lock_member_rows(db, org.tenant_id, org.id, owner)

    return prelock


async def sweep_organization(request: SweepRequest, org: OrgRef) -> RetentionPreview:
    db, now = request.db, request.now
    policy = org_retention(org.settings)
    conversations = 0
    if policy.conversation_days is not None:
        idle = targets.expired_conversations(org, policy.conversation_days, now)
        conversations = await delete_conversations(db, await _ids(db, idle), idle)
    members = targets.expired_members(org, policy.removed_member_days, now)
    owned = targets.member_conversations(org, members)
    member_conversations = await delete_conversations(
        db, await _ids(db, owned), owned, _member_prelock(org)
    )
    memories = 0
    for model in (MemoryRecord, MemoryProposal):
        memories += await delete_in_chunks(db, model, targets.member_memories(org, members, model))
    trashed = targets.expired_agents(org, policy.agent_trash_days, now)
    agents = await purge_expired_agents(db, org.id, trashed, request.checkpointer)
    return RetentionPreview(
        conversations=conversations,
        agents=agents,
        removed_member_conversations=member_conversations,
        removed_member_memories=memories,
    )


@dataclass(frozen=True, slots=True)
class PurgeTarget:
    kind: str
    id: UUID
    name: str
    tenant_id: UUID


async def _record(db: AsyncSession, target: PurgeTarget, counts: RetentionPreview) -> None:
    await audit_service.record_event(
        db,
        actor_type="system",
        action=audit_actions.RETENTION_PURGE,
        target_type=target.kind,
        target_id=target.id,
        target_name_snapshot=target.name,
        outcome="success",
        tenant_id=target.tenant_id,
        metadata={name: value for name, value in counts.model_dump().items() if value},
    )
    await db.commit()


async def sweep_tenant(request: SweepRequest, tenant_id: UUID) -> dict[UUID, RetentionPreview]:
    """Sweep every organization of one company, then its audit rows; returns the counts."""
    db = request.db
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return {}
    days = audit_retention_days(tenant.settings)
    company = PurgeTarget("tenant", tenant.id, tenant.name, tenant.id)
    rows = (
        await db.execute(
            select(Organization.id, Organization.name, Organization.settings)
            .where(
                Organization.tenant_id == tenant_id,
                Organization.status.in_(("active", "suspended")),
            )
            .order_by(Organization.id)
        )
    ).all()
    orgs = [OrgRef(row.id, tenant_id, row.name, dict(row.settings or {})) for row in rows]
    results: dict[UUID, RetentionPreview] = {}
    for org in orgs:
        if not await request.still_leader():
            return results
        counts = await sweep_organization(request, org)
        results[org.id] = counts
        if any(counts.model_dump().values()):
            await _record(db, PurgeTarget("organization", org.id, org.name, tenant_id), counts)
    if not await request.still_leader():
        return results
    expired = await delete_in_chunks(
        db, AuditEvent, targets.expired_audit(tenant_id, days, request.now)
    )
    if expired:
        counts = RetentionPreview(audit_events=expired)
        results[tenant_id] = counts
        await _record(db, company, counts)
    return results
