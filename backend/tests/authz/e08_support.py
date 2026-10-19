"""Seeds for the E-08 retention tests (plan 6.2, 10.7, 14.1)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.memory import MemoryRecord
from app.models.organization import Organization, OrganizationMember
from app.schemas.retention import RetentionPreview
from app.services.retention.job import run_retention_sweep
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TestSession

RETENTION = f"/api/orgs/{TEST_ORG_ID}/retention"
NOW = datetime.now(UTC)


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


async def set_retention(db: AsyncSession, org_id: UUID = TEST_ORG_ID, **values: int | None) -> None:
    org = await db.get(Organization, org_id)
    assert org is not None
    retention: dict[str, JsonValue] = dict(values)
    org.settings = {**(org.settings or {}), "retention": retention}
    await db.commit()


async def add_conversation(
    db: AsyncSession, agent: Agent, *, user: UUID, last_activity: datetime, org: UUID = TEST_ORG_ID
) -> UUID:
    conversation = Conversation(
        agent_id=agent.id,
        user_id=user,
        tenant_id=TEST_TENANT_ID,
        org_id=org,
        agent_name_snapshot=agent.name,
        last_activity_at=last_activity,
        updated_at=last_activity.replace(tzinfo=None),
    )
    db.add(conversation)
    await db.commit()
    return conversation.id


async def add_memory(db: AsyncSession, user: UUID) -> UUID:
    record = MemoryRecord(
        user_id=user,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        scope="user",
        content="likes tea",
        store_path="/memories/user/profile.md",
    )
    db.add(record)
    await db.commit()
    return record.id


async def mark_removed(db: AsyncSession, user: UUID, removed_at: datetime) -> None:
    member = await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.org_id == TEST_ORG_ID, OrganizationMember.user_id == user
        )
    )
    assert member is not None
    member.status, member.removed_at = "removed", removed_at
    await db.commit()


async def exists(model: type, row_id: UUID) -> bool:
    async with TestSession() as db:
        return await db.get(model, row_id) is not None


async def sweep(
    now: datetime = NOW, checkpointer: BaseCheckpointSaver[str] | None = None
) -> dict[UUID, RetentionPreview]:
    results = await run_retention_sweep(maker=TestSession, now=now, checkpointer=checkpointer)
    return results.get(TEST_TENANT_ID, {})
