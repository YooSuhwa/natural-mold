"""What a retention policy selects for deletion; shared by the preview and the sweep.

Sharing the selection is the point: the shortening preview ("대화 1,240건이 삭제돼요")
counts exactly the rows the next sweep would delete under the submitted policy.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.organization import OrganizationMember
from app.schemas.retention import OrgRetention, RetentionPreview
from app.services.retention.idle import idle_before


class OrgScope(Protocol):
    """An organization row or a detached snapshot of its ids."""

    @property
    def id(self) -> UUID: ...

    @property
    def tenant_id(self) -> UUID: ...


@dataclass(frozen=True, slots=True)
class OrgIds:
    id: UUID
    tenant_id: UUID


def cutoff(now: datetime, days: int) -> datetime:
    return now.astimezone(UTC) - timedelta(days=days)


def expired_conversations(org: OrgScope, days: int, now: datetime) -> Select[tuple[UUID]]:
    """Idle since the cutoff (``idle.idle_before``), read-only retained records too."""
    return select(Conversation.id).where(
        Conversation.org_id == org.id,
        Conversation.tenant_id == org.tenant_id,
        idle_before(cutoff(now, days)),
    )


def expired_agents(org: OrgScope, days: int, now: datetime) -> Select[tuple[UUID]]:
    """In the trash (archived) for longer than the trash period."""
    return select(Agent.id).where(
        Agent.org_id == org.id,
        Agent.tenant_id == org.tenant_id,
        Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        Agent.archived_at.is_not(None),
        Agent.archived_at < cutoff(now, days).replace(tzinfo=None),
    )


def expired_members(org: OrgScope, days: int, now: datetime) -> Select[tuple[UUID]]:
    """Removed for longer than ``removed_member_days``; re-adding clears ``removed_at``."""
    return select(OrganizationMember.user_id).where(
        OrganizationMember.org_id == org.id,
        OrganizationMember.tenant_id == org.tenant_id,
        OrganizationMember.status == "removed",
        OrganizationMember.removed_at.is_not(None),
        OrganizationMember.removed_at < cutoff(now, days),
    )


def member_conversations(org: OrgScope, users: Select[tuple[UUID]]) -> Select[tuple[UUID]]:
    return select(Conversation.id).where(
        Conversation.org_id == org.id,
        Conversation.tenant_id == org.tenant_id,
        Conversation.user_id.in_(users.scalar_subquery()),
    )


def member_memories(
    org: OrgScope, users: Select[tuple[UUID]], model: type[MemoryRecord] | type[MemoryProposal]
) -> Select[tuple[UUID]]:
    return select(model.id).where(
        model.org_id == org.id,
        model.tenant_id == org.tenant_id,
        model.user_id.in_(users.scalar_subquery()),
    )


def expired_audit(tenant_id: UUID, days: int, now: datetime) -> Select[tuple[UUID]]:
    """Company audit rows only; events without a company (platform/legacy) are kept."""
    return select(AuditEvent.id).where(
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.created_at < cutoff(now, days).replace(tzinfo=None),
    )


async def count(db: AsyncSession, query: Select[tuple[UUID]]) -> int:
    return int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)


async def preview_org(
    db: AsyncSession, org: OrgScope, policy: OrgRetention, now: datetime
) -> RetentionPreview:
    members = expired_members(org, policy.removed_member_days, now)
    conversations = (
        await count(db, expired_conversations(org, policy.conversation_days, now))
        if policy.conversation_days is not None
        else 0
    )
    memories = await count(db, member_memories(org, members, MemoryRecord))
    memories += await count(db, member_memories(org, members, MemoryProposal))
    return RetentionPreview(
        conversations=conversations,
        agents=await count(db, expired_agents(org, policy.agent_trash_days, now)),
        removed_member_conversations=await count(db, member_conversations(org, members)),
        removed_member_memories=memories,
    )
